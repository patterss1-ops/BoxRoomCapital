"""Tests for K-004 performance attribution live feed."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from analytics.live_attribution import (
    LiveAttributionEngine,
    LivePnL,
    PortfolioSnapshot,
)


# ------------------------------------------------------------------
# LivePnL dataclass
# ------------------------------------------------------------------

class TestLivePnL:
    def test_total_pnl(self):
        pnl = LivePnL(
            strategy="momentum",
            timestamp="2026-03-03T12:00:00+00:00",
            unrealised_pnl=150.0,
            realised_pnl=50.0,
            total_pnl=200.0,
            contribution_pct=40.0,
        )
        assert pnl.total_pnl == 200.0
        assert pnl.unrealised_pnl + pnl.realised_pnl == pnl.total_pnl

    def test_to_dict(self):
        """LivePnL is a plain dataclass; verify all fields are accessible."""
        pnl = LivePnL(
            strategy="mean_revert",
            timestamp="2026-03-03T12:00:00+00:00",
            unrealised_pnl=-30.0,
            realised_pnl=80.0,
            total_pnl=50.0,
            contribution_pct=25.0,
        )
        assert pnl.strategy == "mean_revert"
        assert pnl.timestamp == "2026-03-03T12:00:00+00:00"
        assert pnl.unrealised_pnl == -30.0
        assert pnl.realised_pnl == 80.0
        assert pnl.total_pnl == 50.0
        assert pnl.contribution_pct == 25.0


# ------------------------------------------------------------------
# PortfolioSnapshot dataclass
# ------------------------------------------------------------------

class TestPortfolioSnapshot:
    def test_to_dict(self):
        pnl = LivePnL("strat_a", "ts", 10.0, 20.0, 30.0, 100.0)
        snap = PortfolioSnapshot(
            timestamp="ts",
            total_nav=100_030.0,
            daily_pnl=30.0,
            strategy_pnls=[pnl],
            metadata={"source": "test"},
        )
        d = snap.to_dict()
        assert d["timestamp"] == "ts"
        assert d["total_nav"] == 100_030.0
        assert d["daily_pnl"] == 30.0
        assert len(d["strategy_pnls"]) == 1
        assert d["strategy_pnls"][0]["strategy"] == "strat_a"
        assert d["metadata"] == {"source": "test"}

    def test_defaults(self):
        snap = PortfolioSnapshot(
            timestamp="ts",
            total_nav=100_000.0,
            daily_pnl=0.0,
            strategy_pnls=[],
        )
        assert snap.metadata == {}
        assert snap.strategy_pnls == []


# ------------------------------------------------------------------
# LiveAttributionEngine
# ------------------------------------------------------------------

class TestLiveAttributionEngine:
    def test_update_and_snapshot(self):
        engine = LiveAttributionEngine(["alpha"], initial_nav=50_000.0)
        engine.update_pnl("alpha", unrealised=100.0, realised=50.0)
        snap = engine.take_snapshot()

        assert snap.daily_pnl == 150.0
        assert snap.total_nav == 50_150.0
        assert len(snap.strategy_pnls) == 1
        assert snap.strategy_pnls[0].total_pnl == 150.0

    def test_contribution_percentages(self):
        engine = LiveAttributionEngine(["a", "b"])
        engine.update_pnl("a", unrealised=200.0, realised=0.0)
        engine.update_pnl("b", unrealised=100.0, realised=0.0)
        snap = engine.take_snapshot()

        contribs = {p.strategy: p.contribution_pct for p in snap.strategy_pnls}
        # a=200/(200+100)*100 = 66.67, b=100/300*100 = 33.33
        assert abs(contribs["a"] - 66.6667) < 0.01
        assert abs(contribs["b"] - 33.3333) < 0.01

    def test_multiple_strategies(self):
        strats = ["momentum", "mean_revert", "stat_arb"]
        engine = LiveAttributionEngine(strats)
        engine.update_pnl("momentum", 300.0, 100.0)
        engine.update_pnl("mean_revert", -50.0, 200.0)
        engine.update_pnl("stat_arb", 0.0, 50.0)
        snap = engine.take_snapshot()

        assert len(snap.strategy_pnls) == 3
        total = sum(p.total_pnl for p in snap.strategy_pnls)
        assert snap.daily_pnl == total

    def test_reset_daily(self):
        engine = LiveAttributionEngine(["x", "y"])
        engine.update_pnl("x", 500.0, 200.0)
        engine.update_pnl("y", -100.0, 50.0)
        engine.reset_daily()

        snap = engine.take_snapshot()
        assert snap.daily_pnl == 0.0
        for p in snap.strategy_pnls:
            assert p.unrealised_pnl == 0.0
            assert p.realised_pnl == 0.0
            assert p.total_pnl == 0.0

    def test_history_tracking(self):
        engine = LiveAttributionEngine(["s1"])
        assert engine.history == []

        engine.update_pnl("s1", 10.0, 5.0)
        engine.take_snapshot()
        assert len(engine.history) == 1

        engine.update_pnl("s1", 20.0, 10.0)
        engine.take_snapshot()
        assert len(engine.history) == 2

        # History returns copies so mutations don't affect internal state.
        engine.history.clear()
        assert len(engine.history) == 2

    def test_zero_pnl_handling(self):
        engine = LiveAttributionEngine(["a", "b", "c"])
        # All PnLs are zero — should not raise ZeroDivisionError.
        snap = engine.take_snapshot()
        for p in snap.strategy_pnls:
            assert p.contribution_pct == 0.0
        assert snap.daily_pnl == 0.0

    def test_get_strategy_pnl(self):
        engine = LiveAttributionEngine(["alpha", "beta"])
        engine.update_pnl("alpha", 100.0, 50.0)
        engine.update_pnl("beta", -20.0, 30.0)

        pnl = engine.get_strategy_pnl("alpha")
        assert pnl.strategy == "alpha"
        assert pnl.unrealised_pnl == 100.0
        assert pnl.realised_pnl == 50.0
        assert pnl.total_pnl == 150.0

    def test_unknown_strategy_raises(self):
        engine = LiveAttributionEngine(["only_one"])
        with pytest.raises(KeyError, match="Unknown strategy"):
            engine.update_pnl("nonexistent", 1.0, 2.0)
        with pytest.raises(KeyError, match="Unknown strategy"):
            engine.get_strategy_pnl("nonexistent")

    def test_snapshot_with_mixed_pnl(self):
        """Positive and negative PnLs — contribution uses abs denominator."""
        engine = LiveAttributionEngine(["long", "short"])
        engine.update_pnl("long", 400.0, 0.0)   # total = +400
        engine.update_pnl("short", -200.0, 0.0)  # total = -200
        snap = engine.take_snapshot()

        contribs = {p.strategy: p.contribution_pct for p in snap.strategy_pnls}
        # abs_sum = 400 + 200 = 600
        # long  = 400/600*100 ≈ 66.67
        # short = -200/600*100 ≈ -33.33
        assert abs(contribs["long"] - 66.6667) < 0.01
        assert abs(contribs["short"] - (-33.3333)) < 0.01

        # daily_pnl is net
        assert snap.daily_pnl == 200.0

    def test_json_serialisable(self):
        engine = LiveAttributionEngine(["j1", "j2"])
        engine.update_pnl("j1", 75.0, 25.0)
        engine.update_pnl("j2", -10.0, 40.0)
        snap = engine.take_snapshot()
        d = snap.to_dict()

        # Must not raise
        serialised = json.dumps(d)
        loaded = json.loads(serialised)
        assert loaded["total_nav"] == snap.total_nav
        assert len(loaded["strategy_pnls"]) == 2

    def test_initial_nav_default(self):
        engine = LiveAttributionEngine(["s"])
        engine.update_pnl("s", 0.0, 0.0)
        snap = engine.take_snapshot()
        assert snap.total_nav == 100_000.0

    def test_snapshot_timestamp_is_iso(self):
        engine = LiveAttributionEngine(["t"])
        snap = engine.take_snapshot()
        # Should parse without error as an ISO-8601 timestamp.
        dt = datetime.fromisoformat(snap.timestamp)
        assert dt.tzinfo is not None
