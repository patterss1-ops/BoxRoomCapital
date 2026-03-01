"""Tests for C-001: signal adapter and strategy orchestrator."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from data.trade_db import init_db
from execution.signal_adapter import StrategySlotConfig, signal_to_order_intent
from execution.order_intent import OrderSide, OrderType
from strategies.base import BaseStrategy, Signal, SignalType


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test_orchestrator.db")
    init_db(path)
    return path


def _make_slot(**overrides) -> StrategySlotConfig:
    defaults = {
        "strategy_id": "gtaa",
        "strategy_version": "v1",
        "sleeve": "trend_following",
        "account_type": "SPREADBET",
        "broker_target": "ig",
        "base_qty": 10.0,
        "risk_tags": ["sleeve:trend"],
    }
    defaults.update(overrides)
    return StrategySlotConfig(**defaults)


def _make_signal(
    signal_type: SignalType = SignalType.LONG_ENTRY,
    ticker: str = "SPY",
    strategy_name: str = "gtaa",
    reason: str = "test signal",
    size_multiplier: float = 1.0,
) -> Signal:
    return Signal(
        signal_type=signal_type,
        ticker=ticker,
        strategy_name=strategy_name,
        reason=reason,
        size_multiplier=size_multiplier,
    )


class StubStrategy(BaseStrategy):
    """Test strategy that returns a pre-configured signal."""

    def __init__(self, signal: Signal):
        self._signal = signal

    @property
    def name(self) -> str:
        return self._signal.strategy_name

    def generate_signal(self, ticker, df, current_position, bars_in_trade, **kwargs):
        return Signal(
            signal_type=self._signal.signal_type,
            ticker=ticker,
            strategy_name=self._signal.strategy_name,
            reason=self._signal.reason,
            size_multiplier=self._signal.size_multiplier,
        )


class UniverseAwareStubStrategy(BaseStrategy):
    """Test strategy that checks universe_data is passed."""

    @property
    def name(self) -> str:
        return "universe_aware"

    def generate_signal(self, ticker, df, current_position, bars_in_trade, **kwargs):
        universe_data = kwargs.get("universe_data", {})
        if not universe_data:
            return Signal(SignalType.NONE, ticker, self.name, "no universe data")
        return Signal(
            SignalType.LONG_ENTRY, ticker, self.name,
            f"got {len(universe_data)} tickers in universe",
        )


class ErrorStrategy(BaseStrategy):
    """Test strategy that always raises."""

    @property
    def name(self) -> str:
        return "error_strategy"

    def generate_signal(self, ticker, df, current_position, bars_in_trade, **kwargs):
        raise RuntimeError("intentional test error")


class StubDataProvider:
    """Returns a minimal OHLC DataFrame for any ticker."""

    def get_daily_bars(self, ticker, force_refresh=False):
        dates = pd.date_range("2026-01-01", periods=250, freq="B")
        return pd.DataFrame(
            {
                "Open": 100.0,
                "High": 105.0,
                "Low": 95.0,
                "Close": 102.0,
                "Volume": 1000000,
            },
            index=dates,
        )


# ─── Signal Adapter Tests ─────────────────────────────────────────────────


class TestSignalAdapter:
    def test_long_entry_produces_buy(self):
        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = _make_slot()
        intent = signal_to_order_intent(signal, slot)

        assert intent.side == OrderSide.BUY
        assert intent.instrument == "SPY"
        assert intent.strategy_id == "gtaa"
        assert intent.qty == 10.0
        assert intent.order_type == OrderType.MARKET

    def test_short_entry_produces_sell(self):
        signal = _make_signal(SignalType.SHORT_ENTRY)
        intent = signal_to_order_intent(signal, _make_slot())
        assert intent.side == OrderSide.SELL

    def test_long_exit_produces_sell(self):
        signal = _make_signal(SignalType.LONG_EXIT)
        intent = signal_to_order_intent(signal, _make_slot())
        assert intent.side == OrderSide.SELL
        assert intent.metadata["is_exit"] is True

    def test_short_exit_produces_buy(self):
        signal = _make_signal(SignalType.SHORT_EXIT)
        intent = signal_to_order_intent(signal, _make_slot())
        assert intent.side == OrderSide.BUY
        assert intent.metadata["is_exit"] is True

    def test_none_signal_raises(self):
        signal = _make_signal(SignalType.NONE)
        with pytest.raises(ValueError, match="NONE"):
            signal_to_order_intent(signal, _make_slot())

    def test_size_multiplier_applied(self):
        signal = _make_signal(size_multiplier=0.5)
        slot = _make_slot(base_qty=20.0)
        intent = signal_to_order_intent(signal, slot)
        assert intent.qty == 10.0  # 20 * 0.5

    def test_slot_fields_passed_through(self):
        slot = _make_slot(
            strategy_id="dual_momentum",
            strategy_version="v2",
            sleeve="rotation",
            account_type="ISA",
            broker_target="ibkr",
        )
        signal = _make_signal(strategy_name="dual_momentum")
        intent = signal_to_order_intent(signal, slot)

        assert intent.strategy_id == "dual_momentum"
        assert intent.strategy_version == "v2"
        assert intent.sleeve == "rotation"
        assert intent.broker_target == "ibkr"

    def test_ticker_metadata_merged(self):
        signal = _make_signal()
        metadata = {"epic": "IX.D.SPTRD.DAILY.IP", "currency": "GBP"}
        intent = signal_to_order_intent(signal, _make_slot(), ticker_metadata=metadata)

        assert intent.metadata["epic"] == "IX.D.SPTRD.DAILY.IP"
        assert intent.metadata["currency"] == "GBP"
        assert intent.metadata["signal_reason"] == "test signal"

    def test_reason_in_metadata(self):
        signal = _make_signal(reason="SMA crossover")
        intent = signal_to_order_intent(signal, _make_slot())
        assert intent.metadata["signal_reason"] == "SMA crossover"


# ─── Orchestration Cycle Tests ─────────────────────────────────────────────


class TestOrchestrationCycle:
    def test_actionable_signal_creates_intent(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        assert len(result.signals) == 1
        assert result.signals[0]["signal_type"] == "long_entry"
        assert len(result.intents_created) == 1
        assert result.intents_created[0]["instrument"] == "SPY"
        assert len(result.errors) == 0

    def test_none_signal_no_intent(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        signal = _make_signal(SignalType.NONE)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        assert len(result.signals) == 1
        assert result.signals[0]["signal_type"] == "none"
        assert len(result.intents_created) == 0

    def test_strategy_error_captured(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        slot = StrategySlot(
            strategy=ErrorStrategy(),
            config=_make_slot(strategy_id="error_strategy"),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        assert len(result.errors) == 1
        assert "intentional test error" in result.errors[0]["error"]
        assert len(result.intents_created) == 0

    def test_multiple_slots_independent(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        good_signal = _make_signal(SignalType.LONG_ENTRY, ticker="SPY")

        slots = [
            StrategySlot(
                strategy=StubStrategy(good_signal),
                config=_make_slot(strategy_id="gtaa"),
                tickers=["SPY"],
            ),
            StrategySlot(
                strategy=ErrorStrategy(),
                config=_make_slot(strategy_id="error_strategy"),
                tickers=["EFA"],
            ),
        ]

        result = run_orchestration_cycle(
            slots=slots,
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        # Good slot succeeded
        assert len(result.intents_created) == 1
        assert result.intents_created[0]["instrument"] == "SPY"
        # Bad slot captured error, didn't kill cycle
        assert len(result.errors) == 1
        assert result.errors[0]["strategy_id"] == "error_strategy"

    def test_multiple_tickers_per_slot(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY", "EFA", "IEF"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        assert len(result.signals) == 3
        assert len(result.intents_created) == 3
        tickers = {i["instrument"] for i in result.intents_created}
        assert tickers == {"SPY", "EFA", "IEF"}

    def test_dry_run_logs_shadow_trade(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from data.trade_db import get_shadow_trades

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        shadows = get_shadow_trades(limit=10, db_path=db)
        assert len(shadows) == 1
        assert shadows[0]["ticker"] == "SPY"
        assert shadows[0]["strategy"] == "gtaa"

    def test_live_run_persists_intent(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from data.order_intent_store import get_order_intents

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=False,
            data_provider=StubDataProvider(),
        )

        assert len(result.intents_created) == 1
        assert "intent_id" in result.intents_created[0]

        stored = get_order_intents(limit=10, db_path=db)
        assert len(stored) == 1
        assert stored[0]["instrument"] == "SPY"
        assert stored[0]["status"] == "queued"

    def test_result_summary(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        summary = result.summary()
        assert summary["signals_total"] == 1
        assert summary["intents_created"] == 1
        assert summary["intents_rejected"] == 0
        assert summary["errors"] == 0
        assert "run_id" in summary

    def test_exit_signal_uses_close_action(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from data.trade_db import get_shadow_trades

        signal = _make_signal(SignalType.LONG_EXIT, reason="SMA crossunder")
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        shadows = get_shadow_trades(limit=10, db_path=db)
        assert len(shadows) == 1
        assert shadows[0]["action"] == "close"


# ─── Risk Gate Integration Tests ──────────────────────────────────────────


class TestRiskGateIntegration:
    def test_risk_gate_rejects_oversized_position(self, db):
        """Intent should be rejected when position exceeds max_position_pct_equity."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from risk.pre_trade_gate import RiskLimits

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(base_qty=100.0),  # Large position
            tickers=["SPY"],
        )

        # StubDataProvider returns Close=102.0, so notional = 100 * 102 = 10200
        tight_limits = RiskLimits(
            max_position_pct_equity=5.0,
            max_sleeve_pct_equity=20.0,
            max_correlated_pct_equity=30.0,
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            equity=1000.0,  # 10200 notional → 1020% of 1000 equity
            risk_limits=tight_limits,
        )

        assert len(result.signals) == 1
        assert len(result.intents_created) == 0
        assert len(result.intents_rejected) == 1
        assert result.intents_rejected[0]["reject_rule"] == "MAX_POSITION_PCT_EQUITY"

    def test_risk_gate_approves_within_limits(self, db):
        """Intent should pass when position is within limits."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from risk.pre_trade_gate import RiskLimits

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(base_qty=1.0),  # Small position
            tickers=["SPY"],
        )

        generous_limits = RiskLimits(
            max_position_pct_equity=50.0,
            max_sleeve_pct_equity=80.0,
            max_correlated_pct_equity=90.0,
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            equity=100000.0,  # 1 * 102 = 102 notional → 0.1% of 100k
            risk_limits=generous_limits,
        )

        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0

    def test_no_equity_skips_risk_gate(self, db):
        """When equity=0 (default), risk gate is skipped entirely."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(base_qty=1000.0),  # Would fail risk gate
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            # equity defaults to 0 → risk gate skipped
        )

        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0

    def test_exit_signal_bypasses_risk_gate(self, db):
        """P1-2 regression: exit signals must never be blocked by risk gate."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from risk.pre_trade_gate import RiskLimits

        signal = _make_signal(SignalType.LONG_EXIT, reason="SMA crossunder")
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(base_qty=100.0),
            tickers=["SPY"],
        )

        # Very tight limits that would reject an entry
        tight_limits = RiskLimits(
            max_position_pct_equity=1.0,
            max_sleeve_pct_equity=2.0,
            max_correlated_pct_equity=3.0,
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            equity=100.0,  # Tiny equity — would reject entries
            risk_limits=tight_limits,
        )

        # Exit must pass despite tight limits
        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0

    def test_intra_cycle_cumulative_risk(self, db):
        """P1-3 regression: two entries in same sleeve must accumulate exposure."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from risk.pre_trade_gate import RiskLimits

        signal = _make_signal(SignalType.LONG_ENTRY)

        # Two separate slots in the same sleeve
        slot_a = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(strategy_id="strat_a", base_qty=10.0),
            tickers=["SPY"],
        )
        slot_b = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(strategy_id="strat_b", base_qty=10.0),
            tickers=["EFA"],
        )

        # Sleeve limit of 25%: each entry is 10 * 102 = 1020 notional.
        # With equity=5000, each is 20.4% individually (under 25%).
        # Combined = 40.8% which exceeds 25%.
        limits = RiskLimits(
            max_position_pct_equity=50.0,
            max_sleeve_pct_equity=25.0,
            max_correlated_pct_equity=90.0,
        )

        result = run_orchestration_cycle(
            slots=[slot_a, slot_b],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            equity=5000.0,
            risk_limits=limits,
        )

        # First should pass, second should be rejected by cumulative sleeve check
        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 1
        assert result.intents_rejected[0]["reject_rule"] == "MAX_SLEEVE_PCT_EQUITY"

    def test_notional_uses_close_price(self, db):
        """P1-4 regression: notional should use last close price, not hardcoded *100."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from risk.pre_trade_gate import RiskLimits

        signal = _make_signal(SignalType.LONG_ENTRY)
        # StubDataProvider has Close=102.0, so 5 qty * 102 = 510 notional
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(base_qty=5.0),
            tickers=["SPY"],
        )

        # Position limit at 6% of equity=10000 → threshold = 600
        # Notional = 5 * 102 = 510 → under 600, should pass
        # If it were hardcoded *100, notional = 5 * 100 = 500 → also pass but wrong
        limits = RiskLimits(
            max_position_pct_equity=6.0,
            max_sleeve_pct_equity=80.0,
            max_correlated_pct_equity=90.0,
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            equity=10000.0,
            risk_limits=limits,
        )

        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0


# ─── Router Integration Tests ─────────────────────────────────────────────


class TestRouterIntegration:
    def test_router_kill_switch_rejects(self, db):
        """P1-1 regression: router kill switch should block intents."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from execution.policy.route_policy import RoutePolicyState

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        kill_state = RoutePolicyState(
            kill_switch_active=True,
            kill_switch_reason="Emergency stop",
        )

        # Router with kill switch active (no brokers needed — kill switch is first check)
        from execution.router import AccountRouter
        router = AccountRouter(route_map={}, brokers={})

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            router=router,
            policy_state=kill_state,
        )

        assert len(result.signals) == 1
        assert len(result.intents_created) == 0
        assert len(result.intents_rejected) == 1
        assert result.intents_rejected[0]["reject_rule"] == "kill_switch_active"

    def test_no_router_skips_routing(self, db):
        """When no router is provided, routing validation is skipped."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        signal = _make_signal(SignalType.LONG_ENTRY)
        slot = StrategySlot(
            strategy=StubStrategy(signal),
            config=_make_slot(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
            # No router provided
        )

        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0


# ─── Universe Data Passthrough Tests ──────────────────────────────────────


class TestUniverseDataPassthrough:
    def test_universe_data_passed_to_strategy(self, db):
        """Strategy should receive universe_data with all pre-fetched tickers."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        slot = StrategySlot(
            strategy=UniverseAwareStubStrategy(),
            config=_make_slot(strategy_id="universe_aware"),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot],
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        # UniverseAwareStubStrategy returns LONG_ENTRY if universe_data is present
        assert len(result.signals) == 1
        assert result.signals[0]["signal_type"] == "long_entry"
        assert "got" in result.signals[0]["reason"]

    def test_multi_slot_universe_includes_all_tickers(self, db):
        """Universe data should contain tickers from ALL slots."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle

        slots = [
            StrategySlot(
                strategy=UniverseAwareStubStrategy(),
                config=_make_slot(strategy_id="universe_aware"),
                tickers=["SPY"],
            ),
            StrategySlot(
                strategy=StubStrategy(_make_signal(SignalType.NONE)),
                config=_make_slot(strategy_id="gtaa"),
                tickers=["EFA", "IEF"],
            ),
        ]

        result = run_orchestration_cycle(
            slots=slots,
            db_path=db,
            dry_run=True,
            data_provider=StubDataProvider(),
        )

        # UniverseAwareStub should see 3 tickers in universe (SPY, EFA, IEF)
        spy_signal = [s for s in result.signals if s["ticker"] == "SPY"][0]
        assert "3 tickers" in spy_signal["reason"]
