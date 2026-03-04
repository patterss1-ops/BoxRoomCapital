"""Historical data loader with persistent disk cache.

J-004: Efficient historical price data loading with local SQLite cache,
gap detection, and staleness management. Wraps the existing DataProvider
with a persistent cache layer.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
CACHE_DB_NAME = "historical_data.db"


@dataclass
class CacheEntry:
    """Metadata for a cached ticker."""

    ticker: str
    first_date: str  # YYYY-MM-DD
    last_date: str
    bar_count: int
    cached_at: str  # ISO timestamp
    source: str = "yfinance"


@dataclass
class GapInfo:
    """Detected gap in historical data."""

    ticker: str
    gap_start: str  # YYYY-MM-DD
    gap_end: str
    missing_bars: int


@dataclass
class CacheStats:
    """Overall cache statistics."""

    total_tickers: int = 0
    total_bars: int = 0
    cache_size_bytes: int = 0
    oldest_cache: Optional[str] = None
    newest_cache: Optional[str] = None


class HistoricalCache:
    """Persistent disk cache for historical price data."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        staleness_days: int = 1,
    ):
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._staleness_days = staleness_days
        os.makedirs(self._cache_dir, exist_ok=True)
        self._db_path = os.path.join(self._cache_dir, CACHE_DB_NAME)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS price_bars (
                ticker TEXT NOT NULL,
                bar_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                source TEXT DEFAULT 'yfinance',
                cached_at TEXT NOT NULL,
                PRIMARY KEY (ticker, bar_date)
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS cache_meta (
                ticker TEXT PRIMARY KEY,
                first_date TEXT,
                last_date TEXT,
                bar_count INTEGER,
                source TEXT DEFAULT 'yfinance',
                cached_at TEXT NOT NULL
            )
        """)
        self._db.commit()

    def store_bars(
        self,
        ticker: str,
        bars: list[dict[str, Any]],
        source: str = "yfinance",
    ) -> int:
        """Store OHLCV bars. Each bar must have: date, open, high, low, close, volume.

        Returns number of bars stored.
        """
        if not bars:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        valid = [
            (
                ticker,
                b["date"],
                b.get("open", 0),
                b.get("high", 0),
                b.get("low", 0),
                b.get("close", 0),
                b.get("volume", 0),
                source,
                now,
            )
            for b in bars
            if "date" in b
        ]
        if not valid:
            return 0

        with self._lock:
            self._db.executemany(
                """INSERT OR REPLACE INTO price_bars
                   (ticker, bar_date, open, high, low, close, volume, source, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                valid,
            )
            dates = sorted(b["date"] for b in bars if "date" in b)
            self._db.execute(
                """INSERT OR REPLACE INTO cache_meta
                   (ticker, first_date, last_date, bar_count, source, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ticker, dates[0], dates[-1], len(valid), source, now),
            )
            self._db.commit()
        return len(valid)

    def get_bars(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Retrieve cached bars for a ticker. Returns list of dicts with OHLCV."""
        query = "SELECT bar_date, open, high, low, close, volume FROM price_bars WHERE ticker = ?"
        params: list[Any] = [ticker]

        if start_date:
            query += " AND bar_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND bar_date <= ?"
            params.append(end_date)

        query += " ORDER BY bar_date"
        with self._lock:
            rows = self._db.execute(query, params).fetchall()

        return [
            {
                "date": r[0],
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
            }
            for r in rows
        ]

    def get_entry(self, ticker: str) -> Optional[CacheEntry]:
        """Get cache metadata for a ticker."""
        with self._lock:
            row = self._db.execute(
                "SELECT ticker, first_date, last_date, bar_count, cached_at, source FROM cache_meta WHERE ticker = ?",
                (ticker,),
            ).fetchone()

        if row is None:
            return None

        return CacheEntry(
            ticker=row[0],
            first_date=row[1],
            last_date=row[2],
            bar_count=row[3],
            cached_at=row[4],
            source=row[5],
        )

    def is_stale(self, ticker: str) -> bool:
        """Check if cached data is stale (older than staleness_days)."""
        entry = self.get_entry(ticker)
        if entry is None:
            return True

        cached_dt = datetime.fromisoformat(entry.cached_at)
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - cached_dt
        return age.total_seconds() > self._staleness_days * 86400

    def detect_gaps(
        self,
        ticker: str,
        expected_frequency: str = "daily",
    ) -> list[GapInfo]:
        """Detect gaps in cached data for a ticker."""
        bars = self.get_bars(ticker)
        if len(bars) < 2:
            return []

        gaps = []
        dates = [datetime.strptime(b["date"], "%Y-%m-%d") for b in bars]

        for i in range(1, len(dates)):
            delta = (dates[i] - dates[i - 1]).days
            # Allow weekends (2-3 day gaps are normal for daily data)
            if expected_frequency == "daily" and delta > 4:
                missing = delta - 1  # Approximate missing bars
                gaps.append(GapInfo(
                    ticker=ticker,
                    gap_start=dates[i - 1].strftime("%Y-%m-%d"),
                    gap_end=dates[i].strftime("%Y-%m-%d"),
                    missing_bars=missing,
                ))

        return gaps

    def invalidate(self, ticker: str) -> bool:
        """Remove cached data for a ticker."""
        with self._lock:
            self._db.execute("DELETE FROM price_bars WHERE ticker = ?", (ticker,))
            self._db.execute("DELETE FROM cache_meta WHERE ticker = ?", (ticker,))
            self._db.commit()
        return True

    def get_stats(self) -> CacheStats:
        """Get overall cache statistics."""
        with self._lock:
            total_tickers = self._db.execute("SELECT COUNT(*) FROM cache_meta").fetchone()[0]
            total_bars = self._db.execute("SELECT COALESCE(SUM(bar_count), 0) FROM cache_meta").fetchone()[0]
            oldest = self._db.execute("SELECT MIN(cached_at) FROM cache_meta").fetchone()[0]
            newest = self._db.execute("SELECT MAX(cached_at) FROM cache_meta").fetchone()[0]

        cache_size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0

        return CacheStats(
            total_tickers=total_tickers,
            total_bars=total_bars,
            cache_size_bytes=cache_size,
            oldest_cache=oldest,
            newest_cache=newest,
        )

    def list_tickers(self) -> list[str]:
        """List all cached tickers."""
        with self._lock:
            rows = self._db.execute("SELECT ticker FROM cache_meta ORDER BY ticker").fetchall()
        return [r[0] for r in rows]
