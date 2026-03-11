"""Automated feed aggregator for Engine B intake.

Polls Finnhub (news), Alpha Vantage (analyst revisions), FRED (macro),
and TradingView (news headlines) on staggered intervals. New events are
deduped and submitted to Engine B's intake queue automatically.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

_MAX_DEDUP_ENTRIES = 10_000


class _BoundedHashSet:
    """Set-like container bounded to a max size using insertion order eviction."""

    def __init__(self, max_size: int = _MAX_DEDUP_ENTRIES):
        self._max_size = max(1, max_size)
        self._store: OrderedDict[str, None] = OrderedDict()

    def add(self, key: str) -> bool:
        """Add key. Returns True if key was new, False if already seen."""
        if key in self._store:
            return False
        self._store[key] = None
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)
        return True

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def __len__(self) -> int:
        return len(self._store)


def content_hash(text: str) -> str:
    """Deterministic content hash for dedup."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


class FeedAggregatorService:
    """Background service that polls external feeds and submits events to Engine B."""

    def __init__(
        self,
        *,
        finnhub_client: Any,
        av_client: Any,
        fred_client: Any,
        submit_fn: Callable[..., Any],
        tickers: Sequence[str],
        fred_series: Sequence[str],
        finnhub_interval: int = 300,
        av_interval: int = 900,
        fred_interval: int = 3600,
        tv_client: Any = None,
        tv_interval: int = 600,
        tick_interval: float = 30.0,
    ):
        self._finnhub = finnhub_client
        self._av = av_client
        self._fred = fred_client
        self._tv = tv_client
        self._submit_fn = submit_fn
        self._tickers = list(tickers)
        self._fred_series = list(fred_series)
        self._finnhub_interval = finnhub_interval
        self._av_interval = av_interval
        self._fred_interval = fred_interval
        self._tv_interval = tv_interval
        self._tick_interval = max(5.0, tick_interval)

        self._seen = _BoundedHashSet(_MAX_DEDUP_ENTRIES)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Timing state
        self._last_finnhub_poll: float = 0.0
        self._last_av_poll: float = 0.0
        self._last_fred_poll: float = 0.0
        self._last_tv_poll: float = 0.0

        # Counters
        self._finnhub_submitted: int = 0
        self._av_submitted: int = 0
        self._fred_submitted: int = 0
        self._tv_submitted: int = 0
        self._finnhub_errors: int = 0
        self._av_errors: int = 0
        self._fred_errors: int = 0
        self._tv_errors: int = 0

    # ── Public lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="feed-aggregator",
            daemon=True,
        )
        self._thread.start()
        logger.info("Feed aggregator started")

    def stop(self, timeout: float = 15.0) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("Feed aggregator stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "tickers": list(self._tickers),
                "fred_series": list(self._fred_series),
                "dedup_size": len(self._seen),
                "finnhub": {
                    "submitted": self._finnhub_submitted,
                    "errors": self._finnhub_errors,
                    "last_poll": self._last_finnhub_poll,
                    "interval": self._finnhub_interval,
                },
                "alpha_vantage": {
                    "submitted": self._av_submitted,
                    "errors": self._av_errors,
                    "last_poll": self._last_av_poll,
                    "interval": self._av_interval,
                },
                "fred": {
                    "submitted": self._fred_submitted,
                    "errors": self._fred_errors,
                    "last_poll": self._last_fred_poll,
                    "interval": self._fred_interval,
                },
                "tradingview": {
                    "submitted": self._tv_submitted,
                    "errors": self._tv_errors,
                    "last_poll": self._last_tv_poll,
                    "interval": self._tv_interval,
                    "enabled": self._tv is not None,
                },
            }

    # ── Polling methods ───────────────────────────────────────────────────

    def poll_finnhub_news(self) -> int:
        """Fetch company news per ticker, dedup, submit as news_wire."""
        submitted = 0
        try:
            for ticker in self._tickers:
                articles = self._finnhub.fetch_company_news(ticker, days_back=1)
                for article in articles:
                    key = content_hash(f"finnhub:{article.ticker}:{article.headline}")
                    with self._lock:
                        is_new = self._seen.add(key)
                    if not is_new:
                        continue
                    self._submit_fn(
                        raw_content=f"[{article.ticker}] {article.headline}",
                        source_class="news_wire",
                        source_credibility=0.80,
                        source_ids=[f"finnhub:{article.ticker}:{key}"],
                    )
                    submitted += 1
            with self._lock:
                self._finnhub_submitted += submitted
                self._last_finnhub_poll = time.time()
        except Exception as exc:
            logger.warning("Finnhub poll error: %s", exc)
            with self._lock:
                self._finnhub_errors += 1
        return submitted

    def poll_av_analyst_ratings(self) -> int:
        """Fetch analyst revisions per ticker, dedup, submit as analyst_revision."""
        submitted = 0
        try:
            for ticker in self._tickers:
                revisions = self._av.fetch_analyst_revisions(ticker)
                for rev in revisions:
                    key = content_hash(
                        f"av:{rev.ticker}:{rev.analyst_name}:{rev.revision_date}:{rev.direction.value}"
                    )
                    with self._lock:
                        is_new = self._seen.add(key)
                    if not is_new:
                        continue
                    self._submit_fn(
                        raw_content=(
                            f"[{rev.ticker}] Analyst {rev.analyst_name}: "
                            f"{rev.direction.value} on {rev.estimate_type.value}, "
                            f"change {rev.change_pct:+.1f}%"
                        ),
                        source_class="analyst_revision",
                        source_credibility=0.85,
                        source_ids=[f"av:{rev.ticker}:{key}"],
                    )
                    submitted += 1
            with self._lock:
                self._av_submitted += submitted
                self._last_av_poll = time.time()
        except Exception as exc:
            logger.warning("Alpha Vantage poll error: %s", exc)
            with self._lock:
                self._av_errors += 1
        return submitted

    def poll_tradingview_news(self) -> int:
        """Fetch TradingView news headlines per ticker, dedup, submit as news_wire."""
        if self._tv is None:
            return 0
        submitted = 0
        try:
            for ticker in self._tickers:
                headlines = self._tv.fetch_headlines(ticker)
                for hl in headlines:
                    key = content_hash(f"tv:{hl.headline_id}:{hl.title}")
                    with self._lock:
                        is_new = self._seen.add(key)
                    if not is_new:
                        continue
                    self._submit_fn(
                        raw_content=f"[{hl.ticker}] {hl.title} (via {hl.provider})",
                        source_class="news_wire",
                        source_credibility=0.75,
                        source_ids=[f"tv:{hl.ticker}:{hl.headline_id}"],
                    )
                    submitted += 1
            with self._lock:
                self._tv_submitted += submitted
                self._last_tv_poll = time.time()
        except Exception as exc:
            logger.warning("TradingView news poll error: %s", exc)
            with self._lock:
                self._tv_errors += 1
        return submitted

    def poll_fred_macro(self) -> int:
        """Fetch latest values for key FRED series, dedup, submit as news_wire."""
        submitted = 0
        try:
            for series_id in self._fred_series:
                value = self._fred.fetch_latest_value(series_id)
                if value is None:
                    continue
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                key = content_hash(f"fred:{series_id}:{today_str}:{value}")
                with self._lock:
                    is_new = self._seen.add(key)
                if not is_new:
                    continue
                self._submit_fn(
                    raw_content=f"FRED {series_id} = {value} (as of {today_str})",
                    source_class="news_wire",
                    source_credibility=0.80,
                    source_ids=[f"fred:{series_id}:{today_str}"],
                )
                submitted += 1
            with self._lock:
                self._fred_submitted += submitted
                self._last_fred_poll = time.time()
        except Exception as exc:
            logger.warning("FRED poll error: %s", exc)
            with self._lock:
                self._fred_errors += 1
        return submitted

    # ── Internal loop ─────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.debug("Feed aggregator loop started")
        try:
            while not self._stop_event.is_set():
                now = time.time()
                if now - self._last_finnhub_poll >= self._finnhub_interval:
                    self.poll_finnhub_news()
                if now - self._last_av_poll >= self._av_interval:
                    self.poll_av_analyst_ratings()
                if now - self._last_fred_poll >= self._fred_interval:
                    self.poll_fred_macro()
                if self._tv is not None and now - self._last_tv_poll >= self._tv_interval:
                    self.poll_tradingview_news()
                self._stop_event.wait(timeout=self._tick_interval)
        except Exception as exc:
            logger.error("Feed aggregator loop crashed: %s", exc, exc_info=True)
        finally:
            logger.debug("Feed aggregator loop exited")
