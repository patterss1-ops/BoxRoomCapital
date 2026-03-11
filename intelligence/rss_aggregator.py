"""RSS feed aggregator for Engine B intake.

Polls major financial news RSS feeds on a configurable interval, deduplicates
headlines, caches them in the advisory_rss_cache table, and submits new items
to Engine B's intake queue via the provided submit_fn.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from intelligence.feed_aggregator import _BoundedHashSet, content_hash
from data.trade_db import get_conn, DB_PATH

try:
    import feedparser  # type: ignore[import-untyped]
except ImportError:
    feedparser = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_RSS_FEEDS: dict[str, str] = {
    "ft_markets": "https://www.ft.com/rss/markets",
    "ft_companies": "https://www.ft.com/rss/companies",
    "reuters_markets": "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best",
    "economist_finance": "https://www.economist.com/finance-and-economics/rss.xml",
    "economist_leaders": "https://www.economist.com/leaders/rss.xml",
    "bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bloomberg_markets": "https://feeds.bloomberg.com/markets/news.rss",
    "nikkei_asia": "https://asia.nikkei.com/rss",
    "scmp_economy": "https://www.scmp.com/rss/5/feed",
}

_MAX_DEDUP_ENTRIES = 10_000

_CREATE_CACHE_TABLE = """\
CREATE TABLE IF NOT EXISTS advisory_rss_cache (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    published_at TEXT,
    cached_at TEXT NOT NULL
)
"""


def _ensure_cache_table(db_path: str = DB_PATH) -> None:
    """Create the advisory_rss_cache table if it doesn't exist."""
    conn = get_conn(db_path)
    conn.execute(_CREATE_CACHE_TABLE)
    conn.commit()


class RSSAggregatorService:
    """Background service that polls RSS feeds and submits events to Engine B."""

    def __init__(
        self,
        *,
        feeds: dict[str, str] | None = None,
        submit_fn: Callable[..., Any],
        poll_interval: int = 1800,
        tick_interval: float = 30.0,
        db_path: str = DB_PATH,
    ):
        self._feeds = dict(feeds or DEFAULT_RSS_FEEDS)
        self._submit_fn = submit_fn
        self._poll_interval = poll_interval
        self._tick_interval = max(5.0, tick_interval)
        self._db_path = db_path

        self._seen = _BoundedHashSet(_MAX_DEDUP_ENTRIES)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Timing state
        self._last_poll: float = 0.0

        # Counters
        self._submitted: int = 0
        self._errors: int = 0
        self._feed_errors: dict[str, int] = {}

        # Ensure cache table exists
        try:
            _ensure_cache_table(self._db_path)
        except Exception as exc:
            logger.warning("Could not create advisory_rss_cache table: %s", exc)

    # ── Public lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        if feedparser is None:
            logger.error("feedparser not installed — RSS aggregator cannot start")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="rss-aggregator",
            daemon=True,
        )
        self._thread.start()
        logger.info("RSS aggregator started (%d feeds)", len(self._feeds))

    def stop(self, timeout: float = 15.0) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("RSS aggregator stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "feeds": list(self._feeds.keys()),
                "dedup_size": len(self._seen),
                "submitted": self._submitted,
                "errors": self._errors,
                "feed_errors": dict(self._feed_errors),
                "last_poll": self._last_poll,
                "poll_interval": self._poll_interval,
            }

    # ── Polling ───────────────────────────────────────────────────────────

    def poll_feeds(self) -> int:
        """Fetch all RSS feeds, dedup, cache and submit new items.

        Each feed is polled independently — one feed failing does not
        stop the others.  Returns total number of newly submitted items.
        """
        if feedparser is None:
            logger.warning("feedparser not installed — skipping RSS poll")
            return 0

        total_submitted = 0

        for feed_name, feed_url in self._feeds.items():
            try:
                submitted = self._poll_single_feed(feed_name, feed_url)
                total_submitted += submitted
            except Exception as exc:
                logger.warning("RSS feed '%s' poll error: %s", feed_name, exc)
                with self._lock:
                    self._errors += 1
                    self._feed_errors[feed_name] = (
                        self._feed_errors.get(feed_name, 0) + 1
                    )

        with self._lock:
            self._last_poll = time.time()

        if total_submitted:
            logger.info("RSS poll complete: %d new items submitted", total_submitted)
        else:
            logger.debug("RSS poll complete: no new items")

        return total_submitted

    def _poll_single_feed(self, feed_name: str, feed_url: str) -> int:
        """Parse a single RSS feed and submit new entries."""
        feed = feedparser.parse(feed_url)

        if feed.bozo and not feed.entries:
            logger.debug("RSS feed '%s' returned bozo with no entries", feed_name)
            return 0

        submitted = 0

        for entry in feed.entries:
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            link = getattr(entry, "link", "") or ""

            if not title:
                continue

            # Build dedup key from feed name + title
            hash_value = content_hash(f"rss:{feed_name}:{title}")
            with self._lock:
                is_new = self._seen.add(hash_value)
            if not is_new:
                continue

            # Parse published date
            published_at = ""
            published_parsed = getattr(entry, "published_parsed", None)
            if published_parsed:
                try:
                    published_at = datetime(
                        *published_parsed[:6], tzinfo=timezone.utc
                    ).isoformat()
                except Exception:
                    pass

            # Cache headline for advisor
            try:
                self._cache_headline(
                    self._db_path,
                    source=feed_name,
                    title=title,
                    summary=summary[:500],
                    url=link,
                    published_at=published_at,
                )
            except Exception as exc:
                logger.debug("Cache write failed for '%s': %s", feed_name, exc)

            # Submit to Engine B
            truncated_summary = summary[:200] if summary else ""
            raw_content = f"[{feed_name}] {title}"
            if truncated_summary:
                raw_content += f": {truncated_summary}"

            self._submit_fn(
                raw_content=raw_content,
                source_class="news_wire",
                source_credibility=0.75,
                source_ids=[f"rss:{feed_name}:{hash_value}"],
            )
            submitted += 1

        with self._lock:
            self._submitted += submitted

        return submitted

    # ── Cache ─────────────────────────────────────────────────────────────

    def _cache_headline(
        self,
        db_path: str,
        source: str,
        title: str,
        summary: str,
        url: str,
        published_at: str,
    ) -> None:
        """Cache RSS headline for advisor context."""
        conn = get_conn(db_path)
        row_id = str(uuid.uuid4())
        cached_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO advisory_rss_cache "
            "(id, source, title, summary, url, published_at, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row_id, source, title, summary, url, published_at, cached_at),
        )
        conn.commit()

    # ── Internal loop ─────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.debug("RSS aggregator loop started")
        try:
            while not self._stop_event.is_set():
                now = time.time()
                if now - self._last_poll >= self._poll_interval:
                    self.poll_feeds()
                self._stop_event.wait(timeout=self._tick_interval)
        except Exception as exc:
            logger.error("RSS aggregator loop crashed: %s", exc, exc_info=True)
        finally:
            logger.debug("RSS aggregator loop exited")
