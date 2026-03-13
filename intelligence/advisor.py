"""Advisory engine — conversational investment advisor with persistent memory.

Provides personalised UK investment advice via Telegram with session-based
conversation tracking and long-term memory extraction.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

import requests

import config
from data.historical_cache import HistoricalCache
from data.trade_db import DB_PATH, get_conn

log = logging.getLogger(__name__)

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MEMORY_EXTRACTION_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# DB helpers (module-level, usable independently)
# ---------------------------------------------------------------------------

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS advisor_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    topic TEXT,
    summary TEXT,
    message_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS advisor_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    metadata TEXT,
    FOREIGN KEY (session_id) REFERENCES advisor_sessions(id)
);

CREATE TABLE IF NOT EXISTS advisor_memory (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,
    source_message_id TEXT,
    confidence REAL DEFAULT 1.0,
    expires_at TEXT,
    superseded_by TEXT,
    tags TEXT
);
"""

_ADVISOR_MEMORY_REQUIRED_COLUMNS = {
    "updated_at": "TEXT DEFAULT ''",
    "detail": "TEXT",
    "source_message_id": "TEXT",
    "confidence": "REAL DEFAULT 1.0",
    "expires_at": "TEXT",
    "superseded_by": "TEXT",
    "tags": "TEXT",
}

_MEMORY_TICKER_PATTERN = re.compile(r"(?:\$|\b)([A-Z]{1,5}(?:\.[A-Z]{1,3})?)\b")
_MEMORY_TICKER_STOPWORDS = {
    "ISA",
    "SIPP",
    "GIA",
    "ETF",
    "ETFS",
    "GBP",
    "USD",
    "CASH",
    "UK",
    "USA",
    "EU",
    "VIX",
    "VS",
    "FTSE",
}
_GRAPH_REASON_WEIGHTS = {
    "superseded_by": 3.0,
    "same_session": 2.0,
    "shared_ticker": 1.5,
    "shared_tag": 1.0,
}
_MARKET_QUERY_HINTS = {
    "price",
    "prices",
    "quote",
    "quotes",
    "history",
    "historic",
    "historical",
    "return",
    "returns",
    "performance",
    "performing",
    "compare",
    "comparison",
    "versus",
    "vs",
    "move",
    "moves",
    "moved",
    "chart",
    "charts",
    "today",
    "ytd",
    "month",
    "quarter",
    "year",
}
_MARKET_ALIAS_PATTERNS = (
    (re.compile(r"\b(?:s&p\s*500|sp500|spy)\b", re.IGNORECASE), "SPY"),
    (re.compile(r"\b(?:ftse(?:\s*100)?|ftse100)\b", re.IGNORECASE), "^FTSE"),
    (re.compile(r"\b(?:nasdaq(?:\s*100)?|nasdaq100|qqq)\b", re.IGNORECASE), "QQQ"),
    (re.compile(r"\b(?:dow|dow jones|djia|dia)\b", re.IGNORECASE), "DIA"),
    (re.compile(r"\b(?:russell\s*2000|iwm)\b", re.IGNORECASE), "IWM"),
    (re.compile(r"\b(?:vix|volatility index)\b", re.IGNORECASE), "^VIX"),
)
_MARKET_SYMBOL_DISPLAY_NAMES = {
    "SPY": "S&P 500",
    "^FTSE": "FTSE 100",
    "QQQ": "Nasdaq 100",
    "DIA": "Dow Jones",
    "IWM": "Russell 2000",
    "^VIX": "VIX",
}
_MARKET_RETURN_WINDOWS = (
    ("5d", 5),
    ("1m", 21),
    ("3m", 63),
    ("1y", 252),
)
_MAX_QUERY_MARKET_SYMBOLS = 4
_GENERIC_SESSION_TOPICS = {
    "",
    "advisor chat",
    "chat",
    "conversation",
    "general",
    "general chat",
    "new chat",
}
_LOW_SIGNAL_SESSION_TOPICS = {
    "hello",
    "hey",
    "hi",
    "ok",
    "thanks",
    "thank you",
}
_SESSION_TOPIC_PREFIXES = (
    "can you help me with ",
    "can you help with ",
    "help me with ",
    "help me ",
    "i'm thinking about ",
    "im thinking about ",
    "thinking about ",
    "what do you think about ",
    "what about ",
    "how about ",
    "should i ",
    "can i ",
    "could i ",
    "please ",
)

_schema_init_lock = threading.Lock()
_schema_initialised: set[str] = set()


def _ensure_schema(db_path: str) -> None:
    if db_path in _schema_initialised:
        return
    with _schema_init_lock:
        if db_path in _schema_initialised:
            return
        conn = get_conn(db_path)
        conn.executescript(_INIT_SQL)
        _ensure_table_columns(
            conn,
            "advisor_memory",
            _ADVISOR_MEMORY_REQUIRED_COLUMNS,
        )
        conn.commit()
        _schema_initialised.add(db_path)


def _ensure_table_columns(
    conn,
    table_name: str,
    required_columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, definition in required_columns.items():
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )


def save_advisor_session(
    db_path: str,
    id: str,
    topic: str | None = None,
    summary: str | None = None,
) -> None:
    _ensure_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO advisor_sessions (id, created_at, last_active_at, topic, summary)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               last_active_at = excluded.last_active_at,
               topic = COALESCE(excluded.topic, topic),
               summary = COALESCE(excluded.summary, summary)""",
        (id, now, now, topic, summary),
    )
    conn.commit()


def get_active_session(
    db_path: str, timeout_hours: float = 4,
) -> dict | None:
    _ensure_schema(db_path)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
    ).isoformat()
    conn = get_conn(db_path)
    query = """SELECT id, created_at, last_active_at, topic, summary,
                      message_count, status
               FROM advisor_sessions
               WHERE last_active_at > ? AND status = 'active'"""
    params: list[Any] = [cutoff]
    query += " ORDER BY last_active_at DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    if row is None:
        return None
    return dict(row)


def get_advisor_session(db_path: str, session_id: str) -> dict | None:
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT id, created_at, last_active_at, topic, summary,
                  message_count, status
           FROM advisor_sessions
           WHERE id = ?""",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def save_advisor_message(
    db_path: str,
    id: str,
    session_id: str,
    role: str,
    content: str,
    token_count: int = 0,
    metadata: dict | None = None,
) -> None:
    _ensure_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO advisor_messages (id, session_id, role, content, created_at, token_count, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (id, session_id, role, content, now, token_count, json.dumps(metadata) if metadata else None),
    )
    conn.commit()


def get_advisor_messages(
    db_path: str, session_id: str, limit: int = 20
) -> list[dict]:
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT id, session_id, role, content, created_at, token_count, metadata
           FROM advisor_messages
           WHERE session_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    # Return in chronological order
    return [dict(r) for r in reversed(rows)]


def save_advisor_memory(
    db_path: str,
    id: str,
    topic: str,
    memory_type: str,
    summary: str,
    detail: str | None = None,
    source_message_id: str | None = None,
    confidence: float = 1.0,
    expires_at: str | None = None,
    tags: str | None = None,
) -> None:
    _ensure_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO advisor_memory
               (id, created_at, updated_at, topic, memory_type, summary, detail,
                source_message_id, confidence, expires_at, superseded_by, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
           ON CONFLICT(id) DO UPDATE SET
               updated_at = excluded.updated_at,
               summary = excluded.summary,
               detail = excluded.detail,
               confidence = excluded.confidence,
               tags = excluded.tags""",
        (id, now, now, topic, memory_type, summary, detail, source_message_id,
         confidence, expires_at, tags),
    )
    conn.commit()


def search_advisor_memories(
    db_path: str, query: str, limit: int = 15
) -> list[dict]:
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    # Extract keywords, filter very short tokens
    keywords = [w.strip().lower() for w in query.split() if len(w.strip()) > 2]
    if not keywords:
        return []
    # Build WHERE clause matching any keyword against topic, summary, or tags
    clauses = []
    params: list[Any] = []
    for kw in keywords:
        like = f"%{kw}%"
        clauses.append("(LOWER(topic) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(COALESCE(tags,'')) LIKE ?)")
        params.extend([like, like, like])
    where = " OR ".join(clauses)
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, topic, memory_type, summary, detail, confidence, created_at, tags
            FROM advisor_memory
            WHERE ({where})
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def _topic_is_generic(topic: str | None) -> bool:
    normalized = " ".join(str(topic or "").strip().split()).lower()
    return (
        not normalized
        or normalized in _GENERIC_SESSION_TOPICS
        or normalized in _LOW_SIGNAL_SESSION_TOPICS
    )


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[: max_chars + 1].rsplit(" ", 1)[0].strip()
    return truncated or text[:max_chars].strip()


def _query_mentions_market_data(query: str | None) -> bool:
    lowered = str(query or "").lower()
    return any(hint in lowered for hint in _MARKET_QUERY_HINTS)


def _normalise_symbol_list(symbols: list[str], limit: int = _MAX_QUERY_MARKET_SYMBOLS) -> list[str]:
    deduped: list[str] = []
    for symbol in symbols:
        cleaned = str(symbol or "").strip().upper()
        if not cleaned or cleaned in deduped:
            continue
        deduped.append(cleaned)
        if len(deduped) >= limit:
            break
    return deduped


def _extract_market_symbols_from_query(query: str | None) -> list[str]:
    query_text = str(query or "")
    matches: list[tuple[int, str]] = []
    for pattern, symbol in _MARKET_ALIAS_PATTERNS:
        for match in pattern.finditer(query_text):
            matches.append((match.start(), symbol))

    for match in _MEMORY_TICKER_PATTERN.finditer(query_text):
        symbol = match.group(1).upper()
        if symbol in _MEMORY_TICKER_STOPWORDS:
            continue
        if len(symbol) == 1 and "." not in symbol:
            continue
        matches.append((match.start(1), symbol))

    matches.sort(key=lambda item: item[0])
    return _normalise_symbol_list([symbol for _, symbol in matches])


def _format_signed_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def _flatten_download_frame(frame: Any) -> Any:
    try:
        import pandas as pd

        if frame is not None and hasattr(frame, "columns") and isinstance(frame.columns, pd.MultiIndex):
            frame = frame.copy()
            frame.columns = frame.columns.get_level_values(0)
    except Exception:
        return frame
    return frame


def _history_frame_to_bars(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    frame = _flatten_download_frame(frame)
    bars: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        try:
            close = float(row["Close"])
        except Exception:
            continue
        date_str = index.strftime("%Y-%m-%d") if hasattr(index, "strftime") else str(index)[:10]
        try:
            open_price = float(row["Open"])
        except Exception:
            open_price = close
        try:
            high_price = float(row["High"])
        except Exception:
            high_price = close
        try:
            low_price = float(row["Low"])
        except Exception:
            low_price = close
        try:
            volume = float(row.get("Volume", 0.0))
        except Exception:
            volume = 0.0
        bars.append(
            {
                "date": date_str,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close,
                "volume": volume,
            }
        )
    return bars


def _compute_return_pct(bars: list[dict[str, Any]], lookback_bars: int) -> float | None:
    if len(bars) <= lookback_bars:
        return None
    start_price = float(bars[-(lookback_bars + 1)]["close"] or 0.0)
    end_price = float(bars[-1]["close"] or 0.0)
    if start_price <= 0:
        return None
    return round((end_price / start_price - 1.0) * 100.0, 2)


def _derive_session_topic(text: str | None, max_chars: int = 72) -> str:
    cleaned = " ".join(str(text or "").split()).strip(" -:;,.!?")
    if not cleaned:
        return "General"

    first_segment = re.split(r"(?:[.!?]+\s+|\n+)", cleaned, maxsplit=1)[0].strip(" -:;,.!?")
    lowered = first_segment.lower()
    for prefix in _SESSION_TOPIC_PREFIXES:
        if lowered.startswith(prefix):
            first_segment = first_segment[len(prefix):].strip(" -:;,.!?")
            break

    first_segment = _truncate_text(first_segment.strip(), max_chars)
    if not first_segment:
        return "General"
    return first_segment[0].upper() + first_segment[1:]


def _derive_session_summary(
    user_text: str | None,
    assistant_text: str | None,
    max_chars: int = 160,
) -> str:
    candidate = " ".join(str(assistant_text or user_text or "").split()).strip(" -:;,.!?")
    if not candidate:
        return ""
    first_segment = re.split(r"(?:[.!?]+\s+|\n+)", candidate, maxsplit=1)[0].strip(" -:;,.!?")
    return _truncate_text(first_segment, max_chars)


def update_advisor_session(
    db_path: str,
    session_id: str,
    *,
    topic: str | None = None,
    summary: str | None = None,
    message_count_increment: int = 0,
) -> None:
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE advisor_sessions
           SET last_active_at = ?,
               topic = COALESCE(?, topic),
               summary = COALESCE(?, summary),
               message_count = COALESCE(message_count, 0) + ?
           WHERE id = ?""",
        (
            datetime.now(timezone.utc).isoformat(),
            topic,
            summary,
            message_count_increment,
            session_id,
        ),
    )
    conn.commit()


def list_advisor_sessions(
    db_path: str,
    limit: int = 10,
) -> list[dict]:
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT s.id, s.topic, s.summary, s.last_active_at, s.message_count, s.status,
                  (
                      SELECT msg.content
                      FROM advisor_messages AS msg
                      WHERE msg.session_id = s.id
                        AND msg.role = 'user'
                      ORDER BY msg.created_at ASC
                      LIMIT 1
                  ) AS first_user_message
           FROM advisor_sessions AS s
           ORDER BY s.last_active_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    sessions: list[dict] = []
    for row in rows:
        session = dict(row)
        candidate_topic = session.get("topic")
        if _topic_is_generic(candidate_topic):
            candidate_topic = _derive_session_topic(
                session.get("first_user_message") or session.get("summary")
            )
        session["topic"] = candidate_topic or "General"
        session.pop("first_user_message", None)
        sessions.append(session)
    return sessions


def list_advisor_memories(
    db_path: str,
    limit: int | None = None,
) -> list[dict]:
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    query = """SELECT m.id, m.topic, m.memory_type, m.summary, m.detail,
                      m.confidence, m.created_at, m.updated_at, m.expires_at,
                      m.superseded_by, m.tags, m.source_message_id,
                      msg.session_id
               FROM advisor_memory AS m
               LEFT JOIN advisor_messages AS msg
                 ON msg.id = m.source_message_id
               WHERE (m.expires_at IS NULL OR m.expires_at > datetime('now'))
               ORDER BY m.created_at DESC"""
    params: list[Any] = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _split_memory_tags(raw_tags: str | None) -> list[str]:
    if not raw_tags:
        return []
    tags = []
    for part in raw_tags.split(","):
        cleaned = part.strip().lower()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags


def _extract_memory_tickers(*parts: str | None) -> list[str]:
    tickers: list[str] = []
    for part in parts:
        if not part:
            continue
        for match in _MEMORY_TICKER_PATTERN.findall(part.upper()):
            if match in _MEMORY_TICKER_STOPWORDS:
                continue
            if len(match) == 1 and "." not in match:
                continue
            if match not in tickers:
                tickers.append(match)
    return tickers


def _add_graph_edge(
    edges: dict[tuple[str, str], dict[str, Any]],
    source_id: str,
    target_id: str,
    reason_type: str,
    reason_value: str | None = None,
) -> None:
    if source_id == target_id:
        return
    edge_key = tuple(sorted((source_id, target_id)))
    edge = edges.setdefault(
        edge_key,
        {
            "id": f"{edge_key[0]}::{edge_key[1]}",
            "source": edge_key[0],
            "target": edge_key[1],
            "weight": 0.0,
            "reasons": [],
            "_reason_keys": set(),
        },
    )
    reason_key = (reason_type, str(reason_value or ""))
    if reason_key in edge["_reason_keys"]:
        return
    edge["_reason_keys"].add(reason_key)
    reason = {"type": reason_type}
    if reason_value:
        reason["value"] = reason_value
    edge["reasons"].append(reason)
    edge["weight"] += _GRAPH_REASON_WEIGHTS.get(reason_type, 1.0)


def build_advisor_memory_graph(
    db_path: str,
    limit: int | None = None,
) -> dict[str, Any]:
    memories = list_advisor_memories(db_path, limit=limit)
    nodes_by_id: dict[str, dict[str, Any]] = {}
    session_groups: dict[str, list[str]] = defaultdict(list)
    tag_groups: dict[str, list[str]] = defaultdict(list)
    ticker_groups: dict[str, list[str]] = defaultdict(list)
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    for memory in memories:
        memory_id = str(memory.get("id") or "").strip()
        if not memory_id:
            continue
        tags = _split_memory_tags(memory.get("tags"))
        tickers = _extract_memory_tickers(
            memory.get("topic"),
            memory.get("summary"),
            memory.get("tags"),
        )
        node = {
            "id": memory_id,
            "label": (memory.get("topic") or memory.get("summary") or "note")[:32],
            "topic": memory.get("topic") or "General",
            "memory_type": memory.get("memory_type") or "note",
            "summary": memory.get("summary") or "",
            "detail": memory.get("detail") or "",
            "confidence": float(memory.get("confidence") or 0.0),
            "created_at": memory.get("created_at") or "",
            "updated_at": memory.get("updated_at") or memory.get("created_at") or "",
            "tags": tags,
            "tickers": tickers,
            "source_message_id": memory.get("source_message_id") or "",
            "session_id": memory.get("session_id") or "",
            "superseded_by": memory.get("superseded_by") or "",
        }
        nodes_by_id[memory_id] = node
        if node["session_id"]:
            session_groups[node["session_id"]].append(memory_id)
        for tag in tags:
            tag_groups[tag].append(memory_id)
        for ticker in tickers:
            ticker_groups[ticker].append(memory_id)

    for node in nodes_by_id.values():
        target_id = str(node.get("superseded_by") or "").strip()
        if target_id and target_id in nodes_by_id:
            _add_graph_edge(
                edges,
                node["id"],
                target_id,
                "superseded_by",
                target_id,
            )

    for session_id, memory_ids in session_groups.items():
        for source_id, target_id in combinations(sorted(set(memory_ids)), 2):
            _add_graph_edge(edges, source_id, target_id, "same_session", session_id)

    for tag, memory_ids in tag_groups.items():
        for source_id, target_id in combinations(sorted(set(memory_ids)), 2):
            _add_graph_edge(edges, source_id, target_id, "shared_tag", tag)

    for ticker, memory_ids in ticker_groups.items():
        for source_id, target_id in combinations(sorted(set(memory_ids)), 2):
            _add_graph_edge(edges, source_id, target_id, "shared_ticker", ticker)

    degree_by_id: dict[str, int] = defaultdict(int)
    relationship_counts: dict[str, int] = defaultdict(int)
    edge_list = []
    for edge in edges.values():
        edge["reasons"].sort(
            key=lambda item: _GRAPH_REASON_WEIGHTS.get(item["type"], 0.0),
            reverse=True,
        )
        edge["kind"] = edge["reasons"][0]["type"] if edge["reasons"] else "related"
        degree_by_id[edge["source"]] += 1
        degree_by_id[edge["target"]] += 1
        for reason in edge["reasons"]:
            relationship_counts[reason["type"]] += 1
        edge.pop("_reason_keys", None)
        edge_list.append(edge)

    nodes = sorted(
        (
            {
                **node,
                "degree": degree_by_id.get(node["id"], 0),
            }
            for node in nodes_by_id.values()
        ),
        key=lambda item: (
            -item["degree"],
            -(item["confidence"] or 0.0),
            item["created_at"],
        ),
    )
    edge_list.sort(key=lambda item: (-item["weight"], item["source"], item["target"]))

    return {
        "nodes": nodes,
        "edges": edge_list,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edge_list),
            "isolated_node_count": sum(1 for node in nodes if node["degree"] == 0),
            "relationship_counts": dict(sorted(relationship_counts.items())),
        },
    }


def get_recent_rss_headlines(
    db_path: str, hours: int = 24, limit: int = 20
) -> list[dict]:
    """Fetch recent RSS headlines from advisory_rss_cache if the table exists."""
    _ensure_schema(db_path)
    conn = get_conn(db_path)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = conn.execute(
            """SELECT title, url, published_at, source
               FROM advisory_rss_cache
               WHERE published_at > ?
               ORDER BY published_at DESC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # Table doesn't exist yet — not an error
        return []


# ---------------------------------------------------------------------------
# Advisory Engine
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a knowledgeable UK investment advisor working with a private investor.
Your role is to provide thoughtful, practical investment advice within the UK tax wrapper framework.

**Tax wrappers the investor uses:**
- ISA: £20,000 annual allowance. Tax-free growth and income. Use for core holdings.
- SIPP: £60,000 annual allowance (with tax relief at marginal rate). Locked until age 57. Use for long-term growth.
- GIA: General Investment Account. Capital Gains Tax allowance is £3,000/year. Use for overflow or tactical positions.

**Eligible instruments:** UCITS ETFs, UK-listed investment trusts and funds, individual shares (UK and international via UK platforms).

**Style guidelines:**
- Be direct and opinionated. Give clear recommendations, not wishy-washy "it depends" answers.
- Reference specific tickers and funds where relevant.
- Consider tax efficiency when recommending which wrapper to use.
- Flag risks concisely but don't over-hedge every statement.
- Use GBP as the default currency.
- Keep responses concise — aim for 2-4 paragraphs unless the question demands more.
- When fresh market data is provided below, use it instead of guessing price levels or returns.
- If the user asks for live or historical market data that is missing, say so plainly.

{holdings_section}

{memory_section}

{market_section}

{market_data_section}

{headlines_section}
"""


class AdvisoryEngine:
    """Conversational investment advisor with persistent memory."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        _ensure_schema(self.db_path)
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self._session = requests.Session()
        cache_dir = os.path.join(os.path.dirname(self.db_path) or ".", ".cache")
        self._history_cache = HistoricalCache(cache_dir=cache_dir)

    # -- public interface ----------------------------------------------------

    def process_message(self, chat_id: int, text: str) -> str:
        """Process an incoming message and return the advisor's response."""
        if not self._api_key:
            return "Advisory engine is not configured — ANTHROPIC_API_KEY is missing."

        session = self._get_or_create_session(chat_id)
        session_id = session["id"]

        # Touch session activity timestamp
        save_advisor_session(self.db_path, session_id)

        # Gather context
        memories = self._retrieve_relevant_memories(text)
        recent_msgs = get_advisor_messages(
            self.db_path,
            session_id,
            limit=getattr(config, "ADVISOR_MAX_CONTEXT_MESSAGES", 20),
        )
        holdings = self._get_holdings_snapshot()
        market = self._get_market_context()
        market_data = self._get_query_market_context(text)
        headlines = get_recent_rss_headlines(self.db_path)

        messages = self._build_prompt(
            memories,
            recent_msgs,
            holdings,
            market,
            market_data,
            headlines,
            text,
        )

        try:
            reply = self._call_llm(messages)
        except Exception:
            log.exception("LLM call failed")
            return "Sorry, I'm having trouble thinking right now. Please try again shortly."

        # Persist the exchange
        user_msg_id = str(uuid.uuid4())
        assistant_msg_id = str(uuid.uuid4())
        self._save_exchange(session_id, text, reply, user_msg_id, assistant_msg_id)

        # Background memory extraction
        if getattr(config, "ADVISOR_MEMORY_EXTRACTION_ENABLED", True):
            self._extract_memories_async(session_id, user_msg_id, text, reply)

        return reply

    def recall(self, topic: str) -> str:
        """Search memories for a topic and return a formatted summary."""
        memories = search_advisor_memories(self.db_path, topic)
        if not memories:
            return f"No memories found for '{topic}'."
        lines = [f"**Memories matching '{topic}':**"]
        for m in memories:
            date_str = m["created_at"][:10] if m.get("created_at") else "?"
            lines.append(f"- [{date_str}] {m['summary']}")
        return "\n".join(lines)

    # -- session management --------------------------------------------------

    def _get_or_create_session(self, chat_id: int) -> dict:
        timeout = getattr(config, "ADVISOR_SESSION_TIMEOUT_HOURS", 4)
        session = get_active_session(self.db_path, timeout_hours=timeout)
        if session is not None:
            return session
        session_id = str(uuid.uuid4())
        save_advisor_session(self.db_path, session_id)
        return {
            "id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_active_at": datetime.now(timezone.utc).isoformat(),
            "topic": None,
            "summary": None,
            "message_count": 0,
            "status": "active",
        }

    # -- context retrieval ---------------------------------------------------

    def _retrieve_relevant_memories(self, query: str, limit: int = 15) -> list[dict]:
        max_items = getattr(config, "ADVISOR_MAX_MEMORY_ITEMS", limit)
        return search_advisor_memories(self.db_path, query, limit=max_items)

    def _get_holdings_snapshot(self) -> str:
        try:
            from intelligence.advisory_holdings import format_holdings_telegram
            return format_holdings_telegram()
        except Exception:
            return "No holdings tracked."

    def _get_market_context(self) -> str:
        try:
            import yfinance as yf

            tickers = {"SPY": "S&P 500", "^FTSE": "FTSE 100", "^VIX": "VIX"}
            parts = []
            data = yf.download(
                list(tickers.keys()), period="1d", progress=False, threads=True
            )
            for symbol, label in tickers.items():
                try:
                    if "Close" in data.columns:
                        close = data["Close"]
                        if hasattr(close, "columns") and symbol in close.columns:
                            price = close[symbol].dropna().iloc[-1]
                        elif not hasattr(close, "columns"):
                            price = close.dropna().iloc[-1]
                        else:
                            continue
                        parts.append(f"{label}: {price:,.2f}")
                except Exception:
                    continue
            if parts:
                return "Market snapshot: " + " | ".join(parts)
        except Exception:
            pass
        return "Market data unavailable."

    def _get_portfolio_market_symbols(self, limit: int = _MAX_QUERY_MARKET_SYMBOLS) -> list[str]:
        try:
            from intelligence.advisory_holdings import get_holdings

            holdings = get_holdings(self.db_path)
        except Exception:
            return []
        tickers = [str(holding.get("ticker") or "").strip().upper() for holding in holdings]
        return _normalise_symbol_list(tickers, limit=limit)

    def _fetch_symbol_history(self, symbol: str, min_bars: int = 252) -> list[dict[str, Any]]:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return []

        cached_bars = self._history_cache.get_bars(symbol)
        if cached_bars and len(cached_bars) >= min_bars and not self._history_cache.is_stale(symbol):
            return cached_bars

        try:
            import yfinance as yf

            frame = yf.download(
                symbol,
                period="18mo",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            bars = _history_frame_to_bars(frame)
            if bars:
                self._history_cache.store_bars(symbol, bars, source="yfinance")
                return bars
        except Exception:
            log.debug("History fetch failed for %s", symbol, exc_info=True)

        return cached_bars

    def _get_query_market_context(self, query: str) -> str:
        symbols = _extract_market_symbols_from_query(query)
        lowered = str(query or "").lower()
        if not symbols and _query_mentions_market_data(query):
            if any(token in lowered for token in ("portfolio", "holding", "holdings", "isa", "sipp", "gia")):
                symbols = self._get_portfolio_market_symbols()
        if not symbols:
            return ""

        try:
            from intelligence.advisory_holdings import fetch_live_prices

            live_prices = fetch_live_prices(symbols, self.db_path)
        except Exception:
            live_prices = {}

        lines: list[str] = []
        for symbol in symbols:
            bars = self._fetch_symbol_history(symbol)
            if len(bars) < 2:
                continue
            latest_close = live_prices.get(symbol)
            if latest_close is None:
                latest_close = float(bars[-1]["close"] or 0.0)
            previous_close = float(bars[-2]["close"] or 0.0) if len(bars) >= 2 else 0.0
            day_change = None
            if latest_close and previous_close > 0:
                day_change = round((float(latest_close) / previous_close - 1.0) * 100.0, 2)
            trailing_returns = [
                f"{label} {_format_signed_pct(_compute_return_pct(bars, lookback))}"
                for label, lookback in _MARKET_RETURN_WINDOWS
            ]
            one_year_window = bars[-252:] if len(bars) >= 252 else bars
            range_low = min(float(bar["close"] or 0.0) for bar in one_year_window)
            range_high = max(float(bar["close"] or 0.0) for bar in one_year_window)
            display_name = _MARKET_SYMBOL_DISPLAY_NAMES.get(symbol, symbol)
            lines.append(
                f"- {display_name} ({symbol}): last {float(latest_close):,.2f}, "
                f"day {_format_signed_pct(day_change)}, "
                + ", ".join(trailing_returns)
                + f", 52w range {range_low:,.2f}-{range_high:,.2f}"
            )

        if not lines:
            return ""
        return "**Live market data for this question:**\n" + "\n".join(lines)

    # -- prompt construction -------------------------------------------------

    def _build_prompt(
        self,
        memories: list[dict],
        recent_msgs: list[dict],
        holdings: str,
        market: str,
        market_data: str,
        headlines: list[dict],
        user_msg: str,
    ) -> list[dict]:
        # Build context sections
        if holdings and holdings != "No holdings tracked.":
            holdings_section = f"**Current portfolio:**\n{holdings}"
        else:
            holdings_section = ""

        if memories:
            mem_lines = []
            for m in memories:
                date_str = m["created_at"][:10] if m.get("created_at") else "?"
                mem_lines.append(f"- [{date_str}] {m['summary']}")
            memory_section = "**Relevant past decisions/notes:**\n" + "\n".join(mem_lines)
        else:
            memory_section = ""

        market_section = f"**{market}**" if market else ""
        market_data_section = market_data or ""

        if headlines:
            hl_lines = [f"- {h.get('title', '?')} ({h.get('source', '?')})" for h in headlines[:10]]
            headlines_section = "**Recent headlines:**\n" + "\n".join(hl_lines)
        else:
            headlines_section = ""

        system_text = _SYSTEM_PROMPT.format(
            holdings_section=holdings_section,
            memory_section=memory_section,
            market_section=market_section,
            market_data_section=market_data_section,
            headlines_section=headlines_section,
        ).strip()

        # Build messages array
        messages: list[dict] = []
        for msg in recent_msgs:
            role = msg["role"]
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": msg["content"]})
        messages.append({"role": "user", "content": user_msg})

        return [{"role": "system", "content": system_text}] + messages

    # -- LLM call ------------------------------------------------------------

    def _call_llm(self, messages: list[dict]) -> str:
        """Call Anthropic Messages API and return the text response."""
        model = getattr(config, "ADVISOR_MODEL", "claude-opus-4-6")

        # Separate system message from conversation messages
        system_text = None
        conv_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                conv_messages.append(msg)

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": 2048,
            "messages": conv_messages,
            "temperature": 0.7,
        }
        if system_text:
            body["system"] = system_text

        resp = self._session.post(
            ANTHROPIC_ENDPOINT,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    # -- persistence ---------------------------------------------------------

    def _save_exchange(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        user_msg_id: str | None = None,
        assistant_msg_id: str | None = None,
    ) -> None:
        session = get_advisor_session(self.db_path, session_id) or {}
        topic = None
        if _topic_is_generic(session.get("topic")):
            topic = _derive_session_topic(user_text)
        summary = None
        if not str(session.get("summary") or "").strip():
            summary = _derive_session_summary(user_text, assistant_text)

        uid = user_msg_id or str(uuid.uuid4())
        aid = assistant_msg_id or str(uuid.uuid4())
        save_advisor_message(self.db_path, uid, session_id, "user", user_text)
        save_advisor_message(self.db_path, aid, session_id, "assistant", assistant_text)
        update_advisor_session(
            self.db_path,
            session_id,
            topic=topic,
            summary=summary,
            message_count_increment=2,
        )

    # -- memory extraction ---------------------------------------------------

    _memory_pool: concurrent.futures.ThreadPoolExecutor | None = None

    @classmethod
    def _get_memory_pool(cls) -> concurrent.futures.ThreadPoolExecutor:
        if cls._memory_pool is None:
            cls._memory_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="advisor-mem"
            )
        return cls._memory_pool

    def _extract_memories_async(
        self, session_id: str, source_msg_id: str, user_text: str, assistant_text: str
    ) -> None:
        """Submit memory extraction to a bounded thread pool."""
        self._get_memory_pool().submit(
            self._extract_memories, session_id, source_msg_id, user_text, assistant_text,
        )

    def _extract_memories(
        self, session_id: str, source_msg_id: str, user_text: str, assistant_text: str
    ) -> None:
        """Call a cheaper model to extract structured memories from an exchange."""
        try:
            prompt = f"""Analyze this investment advisor exchange and extract any important decisions,
preferences, observations, or facts worth remembering for future conversations.

USER: {user_text}

ASSISTANT: {assistant_text}

Return a JSON array of memory objects. Each object should have:
- "topic": short topic label (e.g. "ISA allocation", "VWRL position")
- "memory_type": one of "decision", "preference", "observation", "fact", "goal"
- "summary": one-sentence summary
- "tags": comma-separated relevant tags
- "confidence": float 0-1 indicating importance/certainty

If nothing worth remembering, return an empty array: []
Return ONLY valid JSON, no markdown formatting."""

            body = {
                "model": MEMORY_EXTRACTION_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
            resp = self._session.post(
                ANTHROPIC_ENDPOINT,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()["content"][0]["text"].strip()

            # Handle potential markdown code fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            memories = json.loads(raw)
            if not isinstance(memories, list):
                return

            for mem in memories:
                if not isinstance(mem, dict) or "topic" not in mem or "summary" not in mem:
                    continue
                save_advisor_memory(
                    db_path=self.db_path,
                    id=str(uuid.uuid4()),
                    topic=mem["topic"],
                    memory_type=mem.get("memory_type", "observation"),
                    summary=mem["summary"],
                    source_message_id=source_msg_id,
                    confidence=float(mem.get("confidence", 0.8)),
                    tags=mem.get("tags"),
                )
            log.debug("Extracted %d memories from exchange", len(memories))

        except Exception:
            log.debug("Memory extraction failed (non-critical)", exc_info=True)
