"""Background service polling X bookmarks/likes for investment signal.

Follows the FeedAggregatorService pattern: threaded poller with dedup,
periodic polling, and structured submission to Engine B's intake queue.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from data.trade_db import DB_PATH, get_conn
from intelligence.feed_aggregator import _BoundedHashSet, content_hash
from intelligence.x_bookmarks import XBookmarksClient

logger = logging.getLogger(__name__)


class XFeedService:
    """Background service polling X bookmarks/likes for investment signal."""

    def __init__(
        self,
        *,
        client: XBookmarksClient,
        submit_fn: Callable[..., Any],
        db_path: Optional[str] = None,
        poll_interval: int = 1800,
        tick_interval: float = 30.0,
    ):
        self._client = client
        self._submit_fn = submit_fn
        self._db_path = db_path or DB_PATH
        self._poll_interval = max(60, poll_interval)
        self._tick_interval = max(5.0, tick_interval)

        self._seen = _BoundedHashSet(5_000)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Timing state
        self._last_poll: float = 0.0

        # Counters
        self._submitted: int = 0
        self._errors: int = 0

        # Track highest tweet ID to avoid re-fetching
        self._since_id: Optional[str] = None

        # Ensure advisor_memory table exists
        self._ensure_schema()

    # ── Public lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="x-feed-service",
            daemon=True,
        )
        self._thread.start()
        logger.info("X feed service started (poll every %ds)", self._poll_interval)

    def stop(self, timeout: float = 15.0) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("X feed service stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "dedup_size": len(self._seen),
                "submitted": self._submitted,
                "errors": self._errors,
                "last_poll": self._last_poll,
                "poll_interval": self._poll_interval,
                "since_id": self._since_id,
            }

    # ── Polling ────────────────────────────────────────────────────────────

    def poll_likes(self) -> int:
        """Fetch recent likes, dedup, submit to Engine B + save to advisor_memory.

        Returns the number of new tweets submitted.
        """
        submitted = 0
        try:
            tweets = self._client.fetch_likes(
                since_id=self._since_id,
                max_results=20,
            )
            if not tweets:
                with self._lock:
                    self._last_poll = time.time()
                return 0

            for tweet in tweets:
                key = content_hash(f"x:like:{tweet['id']}:{tweet['text'][:200]}")
                with self._lock:
                    is_new = self._seen.add(key)
                if not is_new:
                    continue

                # Submit to Engine B intake
                self._submit_fn(
                    raw_content=f"[X/@{tweet['author']}] {tweet['text'][:500]}",
                    source_class="social_curated",
                    source_credibility=0.70,
                    source_ids=[f"x:like:{tweet['id']}"],
                )

                # Persist to advisor_memory for long-term recall
                self._save_to_memory(tweet)
                submitted += 1

            # Track highest ID for next poll
            max_id = max(tweets, key=lambda t: int(t["id"]))
            if self._since_id is None or int(max_id["id"]) > int(self._since_id):
                self._since_id = max_id["id"]

            with self._lock:
                self._submitted += submitted
                self._last_poll = time.time()
            if submitted:
                logger.info("X feed: submitted %d new liked tweets", submitted)

        except Exception as exc:
            logger.warning("X feed poll error: %s", exc)
            with self._lock:
                self._errors += 1

        return submitted

    # ── Memory persistence ─────────────────────────────────────────────────

    def _save_to_memory(self, tweet: dict) -> None:
        """Save interesting tweet to advisor_memory table."""
        try:
            conn = get_conn(self._db_path)
            topic = self._extract_topic(tweet["text"])
            conn.execute(
                """INSERT OR IGNORE INTO advisor_memory
                   (id, topic, memory_type, summary, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    topic,
                    "bookmark",
                    f"[X/@{tweet['author']}] {tweet['text'][:500]}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Failed to save tweet to advisor_memory: %s", exc)

    @staticmethod
    def _extract_topic(text: str) -> str:
        """Extract a rough topic from tweet text.

        Looks for cashtags ($SPY, $AAPL) first, then common keywords.
        Falls back to 'market_commentary'.
        """
        # Check for cashtags
        import re
        cashtags = re.findall(r"\$([A-Z]{1,5})\b", text.upper())
        if cashtags:
            return cashtags[0].lower()

        # Keyword-based topic extraction
        text_lower = text.lower()
        topic_keywords = {
            "fed": "fed_policy",
            "fomc": "fed_policy",
            "rate": "interest_rates",
            "inflation": "inflation",
            "cpi": "inflation",
            "earnings": "earnings",
            "gdp": "macro_gdp",
            "recession": "recession_risk",
            "oil": "commodities_oil",
            "gold": "commodities_gold",
            "crypto": "crypto",
            "bitcoin": "crypto",
            "etf": "etf_flows",
        }
        for keyword, topic in topic_keywords.items():
            if keyword in text_lower:
                return topic

        return "market_commentary"

    # ── Schema ─────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Create advisor_memory table if it does not exist."""
        try:
            conn = get_conn(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS advisor_memory (
                    id          TEXT PRIMARY KEY,
                    topic       TEXT NOT NULL DEFAULT 'general',
                    memory_type TEXT NOT NULL DEFAULT 'observation',
                    summary     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    metadata    TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_advisor_memory_topic
                ON advisor_memory (topic)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_advisor_memory_type
                ON advisor_memory (memory_type)
            """)
            conn.commit()
        except Exception as exc:
            logger.warning("Could not ensure advisor_memory schema: %s", exc)

    # ── Internal loop ──────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.debug("X feed service loop started")
        try:
            while not self._stop_event.is_set():
                now = time.time()
                if now - self._last_poll >= self._poll_interval:
                    self.poll_likes()
                self._stop_event.wait(timeout=self._tick_interval)
        except Exception as exc:
            logger.error("X feed service loop crashed: %s", exc, exc_info=True)
        finally:
            logger.debug("X feed service loop exited")
