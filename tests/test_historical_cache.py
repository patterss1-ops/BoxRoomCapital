"""Tests for J-004 historical data loader + cache."""

from __future__ import annotations

import os

from data.historical_cache import CacheEntry, CacheStats, GapInfo, HistoricalCache


def _sample_bars(start_day: int = 1, count: int = 10, month: str = "01") -> list[dict]:
    """Generate sample OHLCV bars."""
    bars = []
    for i in range(count):
        day = start_day + i
        bars.append({
            "date": f"2026-{month}-{day:02d}",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1000000 + i * 1000,
        })
    return bars


class TestHistoricalCacheInit:
    def test_creates_cache_dir(self, tmp_path):
        cache_dir = str(tmp_path / "new_cache")
        hc = HistoricalCache(cache_dir=cache_dir)
        assert os.path.isdir(cache_dir)

    def test_db_created(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        assert os.path.exists(os.path.join(str(tmp_path), "historical_data.db"))


class TestStoreAndRetrieve:
    def test_store_bars(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        bars = _sample_bars(count=5)
        stored = hc.store_bars("AAPL", bars)
        assert stored == 5

    def test_retrieve_bars(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        bars = _sample_bars(count=5)
        hc.store_bars("AAPL", bars)

        retrieved = hc.get_bars("AAPL")
        assert len(retrieved) == 5
        assert retrieved[0]["date"] == "2026-01-01"
        assert retrieved[0]["close"] == 100.5

    def test_retrieve_with_date_filter(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        bars = _sample_bars(count=10)
        hc.store_bars("AAPL", bars)

        filtered = hc.get_bars("AAPL", start_date="2026-01-03", end_date="2026-01-07")
        assert len(filtered) == 5

    def test_empty_ticker(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        assert hc.get_bars("NONEXISTENT") == []

    def test_store_empty(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        assert hc.store_bars("AAPL", []) == 0

    def test_upsert_replaces(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        bars = [{"date": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}]
        hc.store_bars("AAPL", bars)

        # Store again with different close
        bars2 = [{"date": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 105, "volume": 1000}]
        hc.store_bars("AAPL", bars2)

        retrieved = hc.get_bars("AAPL")
        assert len(retrieved) == 1
        assert retrieved[0]["close"] == 105


class TestCacheMeta:
    def test_get_entry(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        hc.store_bars("AAPL", _sample_bars(count=5))

        entry = hc.get_entry("AAPL")
        assert entry is not None
        assert entry.ticker == "AAPL"
        assert entry.bar_count == 5
        assert entry.first_date == "2026-01-01"
        assert entry.last_date == "2026-01-05"

    def test_get_entry_missing(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        assert hc.get_entry("NONEXISTENT") is None

    def test_is_stale_fresh(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path), staleness_days=1)
        hc.store_bars("AAPL", _sample_bars(count=3))
        assert hc.is_stale("AAPL") is False

    def test_is_stale_missing(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        assert hc.is_stale("NONEXISTENT") is True


class TestGapDetection:
    def test_no_gaps(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        # Consecutive business days (Mon-Fri)
        bars = _sample_bars(count=5)  # Jan 1-5
        hc.store_bars("AAPL", bars)
        gaps = hc.detect_gaps("AAPL")
        assert len(gaps) == 0

    def test_gap_detected(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        # Big gap: Jan 1 → Jan 15
        bars = [
            {"date": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-01-15", "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1000},
        ]
        hc.store_bars("AAPL", bars)
        gaps = hc.detect_gaps("AAPL")
        assert len(gaps) == 1
        assert gaps[0].gap_start == "2026-01-01"
        assert gaps[0].gap_end == "2026-01-15"
        assert gaps[0].missing_bars > 0


class TestInvalidate:
    def test_invalidate(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        hc.store_bars("AAPL", _sample_bars(count=5))
        assert hc.get_entry("AAPL") is not None

        hc.invalidate("AAPL")
        assert hc.get_entry("AAPL") is None
        assert hc.get_bars("AAPL") == []


class TestStats:
    def test_empty_stats(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        stats = hc.get_stats()
        assert stats.total_tickers == 0
        assert stats.total_bars == 0

    def test_stats_after_store(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        hc.store_bars("AAPL", _sample_bars(count=5))
        hc.store_bars("MSFT", _sample_bars(count=3))

        stats = hc.get_stats()
        assert stats.total_tickers == 2
        assert stats.total_bars == 8
        assert stats.cache_size_bytes > 0

    def test_list_tickers(self, tmp_path):
        hc = HistoricalCache(cache_dir=str(tmp_path))
        hc.store_bars("AAPL", _sample_bars(count=3))
        hc.store_bars("MSFT", _sample_bars(count=3))

        tickers = hc.list_tickers()
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert len(tickers) == 2
