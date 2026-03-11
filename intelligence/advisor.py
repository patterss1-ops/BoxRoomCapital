"""Advisory engine — conversational investment advisor with persistent memory.

Provides personalised UK investment advice via Telegram with session-based
conversation tracking and long-term memory extraction.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import config
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
        _schema_initialised.add(db_path)


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

{holdings_section}

{memory_section}

{market_section}

{headlines_section}
"""


class AdvisoryEngine:
    """Conversational investment advisor with persistent memory."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        _ensure_schema(self.db_path)
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self._session = requests.Session()

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
        headlines = get_recent_rss_headlines(self.db_path)

        messages = self._build_prompt(memories, recent_msgs, holdings, market, headlines, text)

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

    # -- prompt construction -------------------------------------------------

    def _build_prompt(
        self,
        memories: list[dict],
        recent_msgs: list[dict],
        holdings: str,
        market: str,
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

        if headlines:
            hl_lines = [f"- {h.get('title', '?')} ({h.get('source', '?')})" for h in headlines[:10]]
            headlines_section = "**Recent headlines:**\n" + "\n".join(hl_lines)
        else:
            headlines_section = ""

        system_text = _SYSTEM_PROMPT.format(
            holdings_section=holdings_section,
            memory_section=memory_section,
            market_section=market_section,
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
        uid = user_msg_id or str(uuid.uuid4())
        aid = assistant_msg_id or str(uuid.uuid4())
        save_advisor_message(self.db_path, uid, session_id, "user", user_text)
        save_advisor_message(self.db_path, aid, session_id, "assistant", assistant_text)

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
