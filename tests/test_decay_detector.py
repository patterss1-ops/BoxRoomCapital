"""Tests for I-006 strategy performance decay detector."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.trade_db import get_conn, init_db
from analytics.decay_detector import (
    DecayConfig,
    StrategyHealth,
    detect_decay,
    get_decaying_strategies,
)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "decay_test.db")
    init_db(db_path)
    return db_path


def _insert_trades(db_path, strategy, pnls, start_date="2026-01-01"):
    """Insert closed trades for a strategy with sequential dates."""
    conn = get_conn(db_path)
    from datetime import datetime
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    for i, pnl in enumerate(pnls):
        ts = (dt + timedelta(days=i)).isoformat()
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, pnl)
               VALUES (?, 'TEST', ?, 'BUY', 'CLOSE', 1, 100.0, ?)""",
            (ts, strategy, pnl),
        )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Data class
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyHealth:
    def test_to_dict(self):
        h = StrategyHealth(
            strategy="test",
            status="decay",
            flags=["win_rate_low"],
            recent_trades=20,
            recent_win_rate_pct=25.5,
        )
        d = h.to_dict()
        assert d["strategy"] == "test"
        assert d["status"] == "decay"
        assert d["recent_win_rate_pct"] == 25.5


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Core decay detection
# ═══════════════════════════════════════════════════════════════════════════


class TestDecayDetection:
    def test_empty_db_returns_empty(self, db):
        results = detect_decay(db_path=db)
        assert results == []

    def test_disabled_returns_empty(self, db):
        config = DecayConfig(enabled=False)
        _insert_trades(db, "test_strat", [10, 20, 30, -5, 15, 10, 20, -10, 30, 15])
        results = detect_decay(config=config, db_path=db)
        assert results == []

    def test_insufficient_data(self, db):
        _insert_trades(db, "small_strat", [10, 20, -5])
        config = DecayConfig(min_trades=10)
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db)
        assert len(results) == 1
        assert results[0].status == "insufficient_data"

    def test_healthy_strategy(self, db):
        # Good win rate and profit factor
        pnls = [50, -20, 30, 40, -10, 60, 20, -15, 45, 35, 25, -5]
        _insert_trades(db, "good_strat", pnls, start_date="2026-02-01")
        config = DecayConfig(
            min_trades=5,
            lookback_days=60,
            win_rate_floor_pct=35.0,
            profit_factor_floor=0.8,
        )
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db)
        assert len(results) == 1
        assert results[0].status == "healthy"
        assert results[0].recent_win_rate_pct > 35.0

    def test_decaying_strategy_low_win_rate(self, db):
        # Terrible win rate: only 2 wins out of 12
        pnls = [-20, -10, 5, -30, -15, -25, 10, -20, -5, -10, -15, -30]
        _insert_trades(db, "bad_strat", pnls, start_date="2026-02-01")
        config = DecayConfig(
            min_trades=5,
            lookback_days=60,
            win_rate_floor_pct=35.0,
            profit_factor_floor=0.8,
        )
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db)
        assert len(results) == 1
        assert results[0].status in ("decay", "warning")
        assert any("win_rate" in f for f in results[0].flags)

    def test_consecutive_losses_flagged(self, db):
        # 10 consecutive losses at the end
        pnls = [50, 30, 20] + [-10] * 10
        _insert_trades(db, "losing_strat", pnls, start_date="2026-01-15")
        config = DecayConfig(
            min_trades=5,
            lookback_days=60,
            max_consecutive_losses=8,
        )
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db)
        assert len(results) == 1
        assert results[0].consecutive_losses >= 8
        assert any("consecutive" in f for f in results[0].flags)

    def test_multiple_strategies(self, db):
        # One good, one bad
        _insert_trades(db, "good", [50, 30, 40, -10, 60, 20, 30, -5, 25, 40], start_date="2026-02-01")
        _insert_trades(db, "bad", [-20, -10, -30, 5, -15, -25, -10, -20, -5, -10], start_date="2026-02-01")
        config = DecayConfig(min_trades=5, lookback_days=60)
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db)
        assert len(results) == 2
        status_map = {r.strategy: r.status for r in results}
        assert status_map["good"] == "healthy"
        assert status_map["bad"] in ("decay", "warning")

    def test_get_decaying_strategies_filters(self, db):
        _insert_trades(db, "good", [50, 30, 40, -10, 60, 20, 30, -5, 25, 40], start_date="2026-02-01")
        _insert_trades(db, "bad", [-20, -10, -30, 5, -15, -25, -10, -20, -5, -10], start_date="2026-02-01")
        config = DecayConfig(min_trades=5, lookback_days=60)
        decaying = get_decaying_strategies(config=config, report_date="2026-03-03", db_path=db)
        assert all(h.status in ("decay", "warning") for h in decaying)
        assert all(h.strategy != "good" for h in decaying)

    def test_profit_factor_floor(self, db):
        # Profit factor below floor
        pnls = [5, -30, 5, -20, 5, -25, 5, -15, 5, -10]
        _insert_trades(db, "low_pf", pnls, start_date="2026-02-01")
        config = DecayConfig(min_trades=5, lookback_days=60, profit_factor_floor=0.8)
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db)
        assert len(results) == 1
        assert any("profit_factor" in f for f in results[0].flags)

    def test_default_report_date(self, db):
        pnls = [50, 30, 40, -10, 60, 20, 30, -5, 25, 40]
        today = date.today().isoformat()
        _insert_trades(db, "test", pnls, start_date=today)
        results = detect_decay(db_path=db)
        assert len(results) == 1
