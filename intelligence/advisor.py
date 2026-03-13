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
    "theme_related": 1.8,
    "theme_support": 1.2,
}
_MEMORY_PROMOTION_TYPE_SCORES = {
    "decision": 3.0,
    "goal": 2.8,
    "preference": 2.4,
    "fact": 1.9,
    "bookmark": 1.7,
    "observation": 1.2,
    "note": 1.0,
}
_THEME_KEYWORD_GROUPS = (
    ("theme:tax_wrappers", "Tax wrappers", "tax", {"isa", "sipp", "gia", "tax", "allowance", "cgt", "wrapper"}),
    ("theme:risk_posture", "Risk posture", "risk", {"risk", "volatility", "drawdown", "downside", "conservative", "aggressive"}),
    ("theme:goals_constraints", "Goals and constraints", "goals", {"goal", "income", "retirement", "horizon", "liquidity", "withdrawal"}),
    ("theme:portfolio_allocation", "Portfolio allocation", "allocation", {"allocation", "rebalance", "diversification", "core", "satellite", "weight", "portfolio"}),
    ("theme:watchlist_research", "Watchlist and research", "research", {"watchlist", "bookmark", "research", "thesis", "idea"}),
)
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


def _slugify_theme_fragment(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug or "general"


def _derive_memory_theme(node: dict[str, Any]) -> dict[str, str]:
    tickers = [str(ticker).upper() for ticker in node.get("tickers", []) if str(ticker).strip()]
    if tickers:
        primary = tickers[0]
        return {
            "id": f"theme:holding:{primary}",
            "label": f"Holding · {primary}",
            "category": "holding",
        }

    tags = {str(tag).lower() for tag in node.get("tags", []) if str(tag).strip()}
    text = " ".join(
        [
            str(node.get("topic") or ""),
            str(node.get("summary") or ""),
            " ".join(sorted(tags)),
        ]
    ).lower()
    for theme_id, label, category, keywords in _THEME_KEYWORD_GROUPS:
        if tags.intersection(keywords) or any(keyword in text for keyword in keywords):
            return {"id": theme_id, "label": label, "category": category}

    topic_words = re.findall(r"[A-Za-z0-9]+", str(node.get("topic") or ""))[:2]
    topic_label = " ".join(topic_words).title() if topic_words else "General"
    topic_slug = _slugify_theme_fragment(" ".join(topic_words))
    return {
        "id": f"theme:topic:{topic_slug}",
        "label": f"Theme · {topic_label}",
        "category": "topic",
    }


def _score_memory_promotion(
    node: dict[str, Any],
    *,
    raw_degree: int,
    theme_size: int,
    superseded_target_count: int,
) -> float:
    memory_type = str(node.get("memory_type") or "note").lower()
    score = _MEMORY_PROMOTION_TYPE_SCORES.get(memory_type, 1.0)
    score += float(node.get("confidence") or 0.0) * 2.5
    score += min(2.0, raw_degree * 0.45)
    score += min(1.5, max(0, theme_size - 1) * 0.45)
    if node.get("tickers"):
        score += 0.75
    if any(tag in {"isa", "sipp", "gia", "tax", "risk", "allocation", "goal"} for tag in node.get("tags", [])):
        score += 0.4
    if str(node.get("superseded_by") or "").strip():
        score -= 1.25
    if superseded_target_count > 0:
        score += min(1.0, superseded_target_count * 0.35)
    return round(max(0.1, score), 2)


def build_advisor_memory_graph(
    db_path: str,
    limit: int | None = None,
) -> dict[str, Any]:
    memories = list_advisor_memories(db_path, limit=limit)
    raw_nodes_by_id: dict[str, dict[str, Any]] = {}
    session_groups: dict[str, list[str]] = defaultdict(list)
    tag_groups: dict[str, list[str]] = defaultdict(list)
    ticker_groups: dict[str, list[str]] = defaultdict(list)
    theme_groups: dict[str, list[str]] = defaultdict(list)
    raw_edges: dict[tuple[str, str], dict[str, Any]] = {}
    superseded_target_counts: dict[str, int] = defaultdict(int)

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
            "node_kind": "memory",
            "layer": 1,
            "promotion_score": 0.0,
            "promoted": False,
        }
        theme = _derive_memory_theme(node)
        node["theme_id"] = theme["id"]
        node["theme_label"] = theme["label"]
        node["theme_category"] = theme["category"]
        raw_nodes_by_id[memory_id] = node
        theme_groups[theme["id"]].append(memory_id)
        if node["session_id"]:
            session_groups[node["session_id"]].append(memory_id)
        for tag in tags:
            tag_groups[tag].append(memory_id)
        for ticker in tickers:
            ticker_groups[ticker].append(memory_id)
        if node["superseded_by"]:
            superseded_target_counts[node["superseded_by"]] += 1

    for node in raw_nodes_by_id.values():
        target_id = str(node.get("superseded_by") or "").strip()
        if target_id and target_id in raw_nodes_by_id:
            _add_graph_edge(
                raw_edges,
                node["id"],
                target_id,
                "superseded_by",
                target_id,
            )

    for session_id, memory_ids in session_groups.items():
        for source_id, target_id in combinations(sorted(set(memory_ids)), 2):
            _add_graph_edge(raw_edges, source_id, target_id, "same_session", session_id)

    for tag, memory_ids in tag_groups.items():
        for source_id, target_id in combinations(sorted(set(memory_ids)), 2):
            _add_graph_edge(raw_edges, source_id, target_id, "shared_tag", tag)

    for ticker, memory_ids in ticker_groups.items():
        for source_id, target_id in combinations(sorted(set(memory_ids)), 2):
            _add_graph_edge(raw_edges, source_id, target_id, "shared_ticker", ticker)

    raw_degree_by_id: dict[str, int] = defaultdict(int)
    for edge in raw_edges.values():
        raw_degree_by_id[edge["source"]] += 1
        raw_degree_by_id[edge["target"]] += 1

    promoted_memory_ids: set[str] = set()
    theme_nodes_by_id: dict[str, dict[str, Any]] = {}
    for theme_id, memory_ids in theme_groups.items():
        members = [raw_nodes_by_id[memory_id] for memory_id in memory_ids if memory_id in raw_nodes_by_id]
        for member in members:
            member["promotion_score"] = _score_memory_promotion(
                member,
                raw_degree=raw_degree_by_id.get(member["id"], 0),
                theme_size=len(members),
                superseded_target_count=superseded_target_counts.get(member["id"], 0),
            )

        ranked_members = sorted(
            members,
            key=lambda item: (
                -(item["promotion_score"] or 0.0),
                -(item["confidence"] or 0.0),
                item["created_at"],
            ),
        )
        promoted_members: list[dict[str, Any]] = []
        for index, member in enumerate(ranked_members):
            should_promote = (
                index == 0
                or (index < 3 and float(member["promotion_score"] or 0.0) >= 4.2)
            )
            member["promoted"] = should_promote
            if should_promote:
                promoted_members.append(member)
                promoted_memory_ids.add(member["id"])

        theme_tags = sorted(
            {
                tag
                for member in members
                for tag in member.get("tags", [])
            }
        )
        theme_tickers = sorted(
            {
                ticker
                for member in members
                for ticker in member.get("tickers", [])
            }
        )
        supporting_members = promoted_members or ranked_members[:1]
        summary_parts = [
            _truncate_text(str(member.get("summary") or member.get("topic") or ""), 72)
            for member in supporting_members[:2]
            if str(member.get("summary") or member.get("topic") or "").strip()
        ]
        theme_label = str(ranked_members[0].get("theme_label") if ranked_members else theme_id)
        theme_score = round(
            sum(float(member.get("promotion_score") or 0.0) for member in supporting_members) / max(len(supporting_members), 1),
            2,
        )
        top_confidence = round(
            min(
                1.0,
                (
                    sum(float(member.get("confidence") or 0.0) for member in supporting_members)
                    / max(len(supporting_members), 1)
                ) + 0.15,
            ),
            2,
        )
        latest_created = max((str(member.get("created_at") or "") for member in members), default="")
        latest_updated = max((str(member.get("updated_at") or "") for member in members), default="")
        theme_nodes_by_id[theme_id] = {
            "id": theme_id,
            "label": _truncate_text(theme_label, 28),
            "topic": theme_label,
            "memory_type": "theme",
            "summary": "; ".join(summary_parts) or f"{len(members)} supporting memories",
            "detail": f"{len(members)} memories in this theme; {len(promoted_members)} promoted into the graph.",
            "confidence": top_confidence,
            "created_at": latest_created,
            "updated_at": latest_updated or latest_created,
            "tags": theme_tags[:8],
            "tickers": theme_tickers[:6],
            "source_message_id": "",
            "session_id": "",
            "superseded_by": "",
            "node_kind": "theme",
            "layer": 0,
            "promotion_score": theme_score,
            "promoted": True,
            "theme_category": ranked_members[0].get("theme_category", "topic") if ranked_members else "topic",
            "theme_id": theme_id,
            "theme_label": theme_label,
            "evidence_count": len(members),
            "promoted_memory_count": len(promoted_members),
            "supporting_memory_ids": [member["id"] for member in ranked_members],
        }

    display_edges: dict[tuple[str, str], dict[str, Any]] = {}
    for theme_id, theme_node in theme_nodes_by_id.items():
        for memory_id in theme_node.get("supporting_memory_ids", [])[: theme_node.get("promoted_memory_count", 0) or 1]:
            if memory_id in promoted_memory_ids:
                _add_graph_edge(display_edges, theme_id, memory_id, "theme_support", theme_node["topic"])

    for raw_edge in raw_edges.values():
        source_id = str(raw_edge["source"])
        target_id = str(raw_edge["target"])
        source_node = raw_nodes_by_id.get(source_id)
        target_node = raw_nodes_by_id.get(target_id)
        if source_node is None or target_node is None:
            continue

        if source_id in promoted_memory_ids and target_id in promoted_memory_ids:
            for reason in raw_edge.get("reasons", []):
                _add_graph_edge(
                    display_edges,
                    source_id,
                    target_id,
                    str(reason.get("type") or "related"),
                    str(reason.get("value") or "") or None,
                )

        source_theme_id = str(source_node.get("theme_id") or "")
        target_theme_id = str(target_node.get("theme_id") or "")
        if source_theme_id and target_theme_id and source_theme_id != target_theme_id:
            strongest_reason = (raw_edge.get("reasons") or [{}])[0]
            _add_graph_edge(
                display_edges,
                source_theme_id,
                target_theme_id,
                "theme_related",
                str(strongest_reason.get("type") or "related"),
            )

    degree_by_id: dict[str, int] = defaultdict(int)
    relationship_counts: dict[str, int] = defaultdict(int)
    edge_list: list[dict[str, Any]] = []
    for edge in display_edges.values():
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
        [
            {
                **node,
                "degree": degree_by_id.get(node_id, 0),
            }
            for node_id, node in theme_nodes_by_id.items()
        ]
        + [
            {
                **node,
                "degree": degree_by_id.get(node_id, 0),
            }
            for node_id, node in raw_nodes_by_id.items()
            if node_id in promoted_memory_ids
        ],
        key=lambda item: (
            int(item.get("layer", 1)),
            -(item.get("degree") or 0),
            -(float(item.get("promotion_score") or 0.0)),
            -(float(item.get("confidence") or 0.0)),
            item.get("created_at") or "",
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
            "theme_count": len(theme_nodes_by_id),
            "promoted_memory_count": len(promoted_memory_ids),
            "hidden_memory_count": max(0, len(raw_nodes_by_id) - len(promoted_memory_ids)),
            "raw_memory_count": len(raw_nodes_by_id),
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
