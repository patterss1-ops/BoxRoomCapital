"""Tests for app.engine.pipeline — D-001: strategy registry + pipeline wiring.

Covers:
  1. Strategy class registry (register, clear, default population)
  2. build_strategy_slots() — config parsing, validation, error paths
  3. dispatch_orchestration() — scheduler callback wiring
  4. _get_fund_equity() — DB equity lookup with graceful degradation
  5. Integration: config → slots → orchestrator flow
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.engine.pipeline import (
    _get_fund_equity,
    _REQUIRED_FIELDS,
    build_strategy_slots,
    clear_registry,
    dispatch_orchestration,
    get_registered_strategies,
    register_strategy_class,
)
from app.signal.ai_confidence import AIConfidenceGateConfig, ExecutionQualitySnapshot
from app.signal.ai_contracts import AIPanelOpinion, PanelConsensus
from execution.policy.capability_policy import StrategyRequirements
from execution.signal_adapter import StrategySlotConfig
from strategies.base import BaseStrategy, Signal, SignalType


# ─── Fixtures ────────────────────────────────────────────────────────────

class FakeStrategy(BaseStrategy):
    """Minimal strategy for testing registry and slot building."""

    def __init__(self, params: Optional[dict] = None):
        self.p = params or {}

    @property
    def name(self) -> str:
        return "FakeStrategy"

    def generate_signal(self, ticker, df, current_position, bars_in_trade, **kwargs):
        return Signal(SignalType.NONE, ticker, self.name, "test")


class AnotherFakeStrategy(BaseStrategy):
    """Second fake strategy for multi-slot tests."""

    def __init__(self, params: Optional[dict] = None):
        self.p = params or {}

    @property
    def name(self) -> str:
        return "AnotherFake"

    def generate_signal(self, ticker, df, current_position, bars_in_trade, **kwargs):
        return Signal(SignalType.NONE, ticker, self.name, "test")


def _make_slot_config(**overrides) -> dict[str, Any]:
    """Build a valid slot config dict with sane defaults."""
    base = {
        "id": "test_slot",
        "strategy_class": "FakeStrategy",
        "strategy_version": "1.0",
        "params": {},
        "sleeve": "test_sleeve",
        "account_type": "PAPER",
        "broker_target": "paper",
        "tickers": ["SPY", "QQQ"],
        "base_qty": 1.0,
        "risk_tags": ["test"],
        "requirements": {},
        "enabled": True,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset registry before each test for isolation."""
    clear_registry()
    register_strategy_class("FakeStrategy", FakeStrategy)
    register_strategy_class("AnotherFakeStrategy", AnotherFakeStrategy)
    yield
    clear_registry()


@pytest.fixture
def db(tmp_path):
    """Create a minimal test database with required tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_daily_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL UNIQUE,
            total_nav REAL NOT NULL,
            total_cash REAL NOT NULL DEFAULT 0,
            total_positions_value REAL NOT NULL DEFAULT 0,
            unrealised_pnl REAL NOT NULL DEFAULT 0,
            realised_pnl REAL NOT NULL DEFAULT 0,
            daily_return_pct REAL,
            drawdown_pct REAL NOT NULL DEFAULT 0,
            high_water_mark REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            created_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broker_accounts (
            id INTEGER PRIMARY KEY,
            broker_name TEXT,
            account_type TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broker_positions (
            id INTEGER PRIMARY KEY,
            broker_account_id INTEGER,
            ticker TEXT,
            quantity TEXT,
            direction TEXT,
            market_value TEXT DEFAULT '0',
            sleeve TEXT DEFAULT 'unassigned'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_intents (
            id TEXT PRIMARY KEY,
            instrument TEXT,
            strategy_id TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT,
            status TEXT,
            mode TEXT,
            detail TEXT,
            result TEXT,
            error TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            headline TEXT,
            detail TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            strategy TEXT,
            action TEXT,
            size REAL,
            reason TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


# ─── 1. Strategy class registry ─────────────────────────────────────────

class TestStrategyRegistry:
    """Tests for register_strategy_class, get_registered_strategies, clear_registry."""

    def test_register_and_retrieve(self):
        """Registered strategy classes are retrievable by name."""
        registry = get_registered_strategies()
        assert "FakeStrategy" in registry
        assert registry["FakeStrategy"] is FakeStrategy

    def test_register_replaces_existing(self):
        """Registering with same name replaces the previous entry."""
        register_strategy_class("FakeStrategy", AnotherFakeStrategy)
        registry = get_registered_strategies()
        assert registry["FakeStrategy"] is AnotherFakeStrategy

    def test_clear_registry(self):
        """clear_registry() empties the registry."""
        clear_registry()
        assert get_registered_strategies() == {}

    def test_get_returns_copy(self):
        """get_registered_strategies returns a copy, not the internal dict."""
        reg1 = get_registered_strategies()
        reg1["SomeNewClass"] = FakeStrategy
        reg2 = get_registered_strategies()
        assert "SomeNewClass" not in reg2

    def test_register_empty_name_raises(self):
        """Empty string name raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            register_strategy_class("", FakeStrategy)

    def test_register_whitespace_name_raises(self):
        """Whitespace-only name raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            register_strategy_class("   ", FakeStrategy)

    def test_multiple_classes_registered(self):
        """Multiple classes can coexist in the registry."""
        registry = get_registered_strategies()
        assert len(registry) == 2
        assert "FakeStrategy" in registry
        assert "AnotherFakeStrategy" in registry

    def test_default_registry_populates_when_empty(self):
        """_ensure_default_registry populates GTAA and DualMomentum when empty."""
        clear_registry()
        # Import and call _ensure_default_registry
        from app.engine.pipeline import _ensure_default_registry
        _ensure_default_registry()
        registry = get_registered_strategies()
        assert "GTAAStrategy" in registry
        assert "DualMomentumStrategy" in registry

    def test_default_registry_no_op_when_populated(self):
        """_ensure_default_registry does nothing if registry already has entries."""
        from app.engine.pipeline import _ensure_default_registry
        # Registry already has FakeStrategy from fixture
        _ensure_default_registry()
        registry = get_registered_strategies()
        # Should still have Fake, not GTAA (because it was non-empty)
        assert "FakeStrategy" in registry


# ─── 2. build_strategy_slots ────────────────────────────────────────────

class TestBuildStrategySlots:
    """Tests for build_strategy_slots() config parsing."""

    def test_builds_single_slot(self):
        """Single valid config builds one StrategySlot."""
        slots = build_strategy_slots([_make_slot_config()])
        assert len(slots) == 1
        slot = slots[0]
        assert slot.config.strategy_id == "test_slot"
        assert slot.config.strategy_version == "1.0"
        assert slot.config.sleeve == "test_sleeve"
        assert slot.config.account_type == "PAPER"
        assert slot.config.broker_target == "paper"
        assert slot.config.base_qty == 1.0
        assert slot.tickers == ["SPY", "QQQ"]
        assert isinstance(slot.strategy, FakeStrategy)

    def test_builds_multiple_slots(self):
        """Multiple configs build correct number of slots."""
        configs = [
            _make_slot_config(id="slot_a", strategy_class="FakeStrategy"),
            _make_slot_config(id="slot_b", strategy_class="AnotherFakeStrategy"),
        ]
        slots = build_strategy_slots(configs)
        assert len(slots) == 2
        assert slots[0].config.strategy_id == "slot_a"
        assert slots[1].config.strategy_id == "slot_b"
        assert isinstance(slots[0].strategy, FakeStrategy)
        assert isinstance(slots[1].strategy, AnotherFakeStrategy)

    def test_disabled_slot_skipped(self):
        """Disabled slots are not included in output."""
        configs = [
            _make_slot_config(id="active", enabled=True),
            _make_slot_config(id="disabled", enabled=False),
        ]
        slots = build_strategy_slots(configs)
        assert len(slots) == 1
        assert slots[0].config.strategy_id == "active"

    def test_enabled_defaults_true(self):
        """Slots without explicit enabled flag default to True."""
        cfg = _make_slot_config()
        del cfg["enabled"]
        slots = build_strategy_slots([cfg])
        assert len(slots) == 1

    def test_empty_config_returns_empty_list(self):
        """Empty config list returns empty slots list."""
        slots = build_strategy_slots([])
        assert slots == []

    def test_reads_from_config_module(self):
        """When slot_configs is None, reads from config.STRATEGY_SLOTS."""
        import config as real_config
        original = getattr(real_config, "STRATEGY_SLOTS", [])
        try:
            real_config.STRATEGY_SLOTS = [_make_slot_config()]
            slots = build_strategy_slots(slot_configs=None)
            assert len(slots) == 1
        finally:
            real_config.STRATEGY_SLOTS = original

    def test_missing_strategy_slots_in_config(self):
        """If config module has no STRATEGY_SLOTS, returns empty list."""
        import config as real_config
        original = getattr(real_config, "STRATEGY_SLOTS", [])
        try:
            del real_config.STRATEGY_SLOTS
            slots = build_strategy_slots(slot_configs=None)
            assert slots == []
        finally:
            real_config.STRATEGY_SLOTS = original

    # ── Validation errors ────────────────────────────────────────────

    def test_missing_required_field_raises(self):
        """Missing required fields raise ValueError with field names."""
        cfg = _make_slot_config()
        del cfg["id"]
        del cfg["sleeve"]
        with pytest.raises(ValueError, match="missing required fields"):
            build_strategy_slots([cfg])

    def test_unknown_strategy_class_raises(self):
        """Unknown strategy_class raises ValueError with suggestions."""
        cfg = _make_slot_config(strategy_class="NonExistentStrategy")
        with pytest.raises(ValueError, match="Unknown strategy class"):
            build_strategy_slots([cfg])

    def test_empty_tickers_raises(self):
        """Empty tickers list raises ValueError."""
        cfg = _make_slot_config(tickers=[])
        with pytest.raises(ValueError, match="empty tickers"):
            build_strategy_slots([cfg])

    # ── Params and optional fields ───────────────────────────────────

    def test_params_passed_to_strategy(self):
        """Custom params are passed to the strategy constructor."""
        custom_params = {"lookback": 50, "threshold": 0.5}
        cfg = _make_slot_config(params=custom_params)
        slots = build_strategy_slots([cfg])
        assert slots[0].strategy.p == custom_params

    def test_default_params_empty_dict(self):
        """Missing params key defaults to empty dict."""
        cfg = _make_slot_config()
        del cfg["params"]
        slots = build_strategy_slots([cfg])
        assert slots[0].strategy.p == {}

    def test_risk_tags_parsed(self):
        """Risk tags are parsed from config."""
        cfg = _make_slot_config(risk_tags=["mean_reversion", "daily"])
        slots = build_strategy_slots([cfg])
        assert slots[0].config.risk_tags == ["mean_reversion", "daily"]

    def test_default_risk_tags_empty(self):
        """Missing risk_tags defaults to empty list."""
        cfg = _make_slot_config()
        del cfg["risk_tags"]
        slots = build_strategy_slots([cfg])
        assert slots[0].config.risk_tags == []

    def test_requirements_parsed(self):
        """Requirements dict is parsed into StrategyRequirements."""
        cfg = _make_slot_config(requirements={
            "requires_spot_etf": True,
            "requires_short": True,
        })
        slots = build_strategy_slots([cfg])
        req = slots[0].requirements
        assert req.requires_spot_etf is True
        assert req.requires_short is True
        assert req.requires_options is False  # default

    def test_default_requirements_all_false(self):
        """Missing requirements defaults to all-False StrategyRequirements."""
        cfg = _make_slot_config()
        del cfg["requirements"]
        slots = build_strategy_slots([cfg])
        req = slots[0].requirements
        assert req.requires_spot_etf is False
        assert req.requires_short is False

    def test_base_qty_coerced_to_float(self):
        """Integer base_qty is coerced to float."""
        cfg = _make_slot_config(base_qty=5)
        slots = build_strategy_slots([cfg])
        assert isinstance(slots[0].config.base_qty, float)
        assert slots[0].config.base_qty == 5.0

    def test_tickers_are_list_copy(self):
        """Tickers list in slot is a copy, not a reference."""
        original = ["SPY", "QQQ"]
        cfg = _make_slot_config(tickers=original)
        slots = build_strategy_slots([cfg])
        slots[0].tickers.append("DIA")
        assert "DIA" not in original

    def test_strategy_version_preserved(self):
        """Strategy version string is preserved in config."""
        cfg = _make_slot_config(strategy_version="2.3.1")
        slots = build_strategy_slots([cfg])
        assert slots[0].config.strategy_version == "2.3.1"


# ─── 3. dispatch_orchestration ──────────────────────────────────────────

class TestDispatchOrchestration:
    """Tests for dispatch_orchestration() — the scheduler callback."""

    def test_returns_orchestration_result(self, db):
        """dispatch_orchestration returns an OrchestrationResult."""
        from app.engine.orchestrator import OrchestrationResult

        # Use a mock data_provider so we don't need real market data
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None  # No data = no signals

        result = dispatch_orchestration(
            window_name="test_window",
            db_path=db,
            dry_run=True,
            slot_configs=[_make_slot_config()],
            data_provider=mock_dp,
        )

        assert isinstance(result, OrchestrationResult)
        assert hasattr(result, "summary")

    def test_summary_has_required_keys(self, db):
        """OrchestrationResult.summary() contains expected keys."""
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None

        result = dispatch_orchestration(
            window_name="test",
            db_path=db,
            dry_run=True,
            slot_configs=[_make_slot_config()],
            data_provider=mock_dp,
        )
        summary = result.summary()
        assert "run_id" in summary
        assert "signals_total" in summary
        assert "intents_created" in summary
        assert "intents_rejected" in summary
        assert "errors" in summary

    def test_empty_slots_returns_empty_result(self, db):
        """No configured slots returns an empty result."""
        result = dispatch_orchestration(
            window_name="test",
            db_path=db,
            dry_run=True,
            slot_configs=[],
        )
        summary = result.summary()
        assert summary["run_id"] == "empty"
        assert summary["signals_total"] == 0
        assert summary["intents_created"] == 0

    def test_dry_run_passed_through(self, db):
        """dry_run=True is forwarded to the orchestrator."""
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None

        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(
                run_id="test", run_at="now"
            )

            dispatch_orchestration(
                window_name="test",
                db_path=db,
                dry_run=True,
                slot_configs=[_make_slot_config()],
                data_provider=mock_dp,
            )

            _, kwargs = mock_orch.call_args
            assert kwargs["dry_run"] is True

    def test_equity_override_used(self, db):
        """Explicit equity parameter overrides DB lookup."""
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None

        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(
                run_id="test", run_at="now"
            )

            dispatch_orchestration(
                window_name="test",
                db_path=db,
                dry_run=True,
                slot_configs=[_make_slot_config()],
                data_provider=mock_dp,
                equity=50000.0,
            )

            _, kwargs = mock_orch.call_args
            assert kwargs["equity"] == 50000.0

    def test_equity_from_db(self, db):
        """When equity is None, reads from fund_daily_report table."""
        # Insert a NAV record
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO fund_daily_report (report_date, total_nav) VALUES (?, ?)",
            ("2026-03-01", 75000.0),
        )
        conn.commit()
        conn.close()

        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None

        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(
                run_id="test", run_at="now"
            )

            dispatch_orchestration(
                window_name="test",
                db_path=db,
                dry_run=True,
                slot_configs=[_make_slot_config()],
                data_provider=mock_dp,
                equity=None,
            )

            _, kwargs = mock_orch.call_args
            assert kwargs["equity"] == 75000.0

    def test_scheduler_compatible_signature(self, db):
        """dispatch_orchestration accepts scheduler's exact kwargs."""
        # The scheduler calls with: window_name, db_path, dry_run
        # This test verifies the function accepts these without error.
        # We use slot_configs to isolate from real config (scheduler compat
        # is about the 3 required params, not config source).
        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(
                run_id="test", run_at="now"
            )

            # Simulate scheduler call — exactly these 3 kwargs
            result = dispatch_orchestration(
                window_name="us_close_orchestration",
                db_path=db,
                dry_run=False,
                slot_configs=[_make_slot_config()],
            )

            assert hasattr(result, "summary")

    def test_scheduler_call_with_real_config(self, db):
        """dispatch_orchestration works with real config.STRATEGY_SLOTS."""
        # Ensure real strategies are registered
        clear_registry()
        from app.engine.pipeline import _ensure_default_registry
        _ensure_default_registry()

        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(
                run_id="test", run_at="now"
            )

            # This simulates the real scheduler call path
            result = dispatch_orchestration(
                window_name="us_close_orchestration",
                db_path=db,
                dry_run=True,
            )

            assert hasattr(result, "summary")
            # Verify slots were built from real config
            call_args = mock_orch.call_args
            assert len(call_args.kwargs["slots"]) == 2  # gtaa + dual_momentum

    def test_ai_overrides_passed_through(self, db):
        """Explicit AI consensus/quality/config are forwarded to orchestrator."""
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None
        consensus = PanelConsensus(
            ticker="SPY",
            as_of="2026-03-03T00:00:00Z",
            consensus_opinion=AIPanelOpinion.BUY,
            consensus_confidence=0.8,
            consensus_score=0.6,
            agreement_ratio=0.75,
            opinion_distribution={"buy": 3},
            models_responded=3,
            models_failed=0,
            verdicts=(),
            failed_models=(),
            provenance_hash="abc123",
        )
        quality = ExecutionQualitySnapshot(
            fill_rate_pct=90.0,
            reject_rate_pct=5.0,
            mean_slippage_bps=12.0,
            sample_count=100,
        )
        gate_cfg = AIConfidenceGateConfig(min_calibrated_confidence=0.6)

        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(run_id="test", run_at="now")

            dispatch_orchestration(
                window_name="test",
                db_path=db,
                dry_run=True,
                slot_configs=[_make_slot_config()],
                data_provider=mock_dp,
                ai_consensus_by_ticker={"SPY": consensus},
                ai_execution_quality=quality,
                ai_gate_config=gate_cfg,
            )

            _, kwargs = mock_orch.call_args
            assert kwargs["ai_consensus_by_ticker"]["SPY"] == consensus
            assert kwargs["ai_execution_quality"] == quality
            assert kwargs["ai_gate_config"] == gate_cfg

    def test_ai_panel_enabled_builds_consensus_and_quality(self, db):
        """When enabled, dispatch builds AI consensus + quality snapshot."""
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None
        consensus = PanelConsensus(
            ticker="SPY",
            as_of="2026-03-03T00:00:00Z",
            consensus_opinion=AIPanelOpinion.BUY,
            consensus_confidence=0.8,
            consensus_score=0.6,
            agreement_ratio=0.75,
            opinion_distribution={"buy": 3},
            models_responded=3,
            models_failed=0,
            verdicts=(),
            failed_models=(),
            provenance_hash="abc123",
        )
        quality = ExecutionQualitySnapshot(
            fill_rate_pct=88.0,
            reject_rate_pct=7.0,
            mean_slippage_bps=15.0,
            sample_count=80,
        )

        with patch("app.engine.pipeline._collect_ai_panel_consensus") as mock_collect:
            with patch("app.engine.pipeline._build_execution_quality_snapshot") as mock_quality:
                with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
                    from app.engine.orchestrator import OrchestrationResult

                    mock_collect.return_value = {"SPY": consensus}
                    mock_quality.return_value = quality
                    mock_orch.return_value = OrchestrationResult(run_id="test", run_at="now")

                    dispatch_orchestration(
                        window_name="test",
                        db_path=db,
                        dry_run=True,
                        slot_configs=[_make_slot_config()],
                        data_provider=mock_dp,
                        ai_panel_enabled=True,
                    )

                    mock_collect.assert_called_once()
                    mock_quality.assert_called_once()
                    _, kwargs = mock_orch.call_args
                    assert kwargs["ai_consensus_by_ticker"]["SPY"] == consensus
                    assert kwargs["ai_execution_quality"] == quality


# ─── 4. _get_fund_equity ────────────────────────────────────────────────

class TestGetFundEquity:
    """Tests for the fund equity lookup helper."""

    def test_returns_latest_nav(self, db):
        """Returns the most recent fund_daily_report total_nav value."""
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO fund_daily_report (report_date, total_nav) VALUES (?, ?)",
            ("2026-02-28", 50000.0),
        )
        conn.execute(
            "INSERT INTO fund_daily_report (report_date, total_nav) VALUES (?, ?)",
            ("2026-03-01", 55000.0),
        )
        conn.commit()
        conn.close()

        result = _get_fund_equity(db)
        assert result == 55000.0

    def test_returns_zero_when_empty(self, db):
        """Returns 0.0 when fund_daily_report table is empty."""
        result = _get_fund_equity(db)
        assert result == 0.0

    def test_returns_zero_when_table_missing(self, tmp_path):
        """Returns 0.0 when fund_daily_report table doesn't exist."""
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.close()

        result = _get_fund_equity(db_path)
        assert result == 0.0

    def test_returns_zero_on_db_error(self):
        """Returns 0.0 on database connection error."""
        result = _get_fund_equity("/nonexistent/path/db.sqlite")
        assert result == 0.0


# ─── 5. Integration: config → slots → orchestrator ──────────────────────

class TestConfigIntegration:
    """Integration tests for the full config → slots → dispatch flow."""

    def test_real_gtaa_strategy_builds(self):
        """GTAAStrategy can be instantiated from config with real params."""
        clear_registry()
        from app.engine.pipeline import _ensure_default_registry
        _ensure_default_registry()

        import config
        cfg = {
            "id": "gtaa_integration",
            "strategy_class": "GTAAStrategy",
            "strategy_version": "1.0",
            "params": config.GTAA_PARAMS,
            "sleeve": "sleeve_6_rotation",
            "account_type": "ISA",
            "broker_target": "ibkr",
            "tickers": config.GTAA_PARAMS["universe"],
            "base_qty": 1.0,
            "risk_tags": ["trend_following"],
            "requirements": {"requires_spot_etf": True},
            "enabled": True,
        }
        slots = build_strategy_slots([cfg])
        assert len(slots) == 1
        assert slots[0].config.strategy_id == "gtaa_integration"
        assert slots[0].tickers == ["SPY", "EFA", "IEF", "VNQ", "DBC"]
        # Verify strategy params merged correctly
        assert slots[0].strategy.p["sma_period"] == 200
        assert slots[0].strategy.p["rebalance_day"] == 1

    def test_real_dual_momentum_strategy_builds(self):
        """DualMomentumStrategy can be instantiated from config with real params."""
        clear_registry()
        from app.engine.pipeline import _ensure_default_registry
        _ensure_default_registry()

        import config
        cfg = {
            "id": "dm_integration",
            "strategy_class": "DualMomentumStrategy",
            "strategy_version": "1.0",
            "params": config.DUAL_MOMENTUM_PARAMS,
            "sleeve": "sleeve_6_rotation",
            "account_type": "ISA",
            "broker_target": "ibkr",
            "tickers": ["SPY", "EFA", "AGG"],
            "base_qty": 1.0,
            "risk_tags": ["momentum"],
            "requirements": {"requires_spot_etf": True},
            "enabled": True,
        }
        slots = build_strategy_slots([cfg])
        assert len(slots) == 1
        assert slots[0].config.strategy_id == "dm_integration"
        assert slots[0].strategy.p["lookback_days"] == 252

    def test_config_strategy_slots_parses(self):
        """The actual config.STRATEGY_SLOTS list builds valid slots."""
        clear_registry()
        from app.engine.pipeline import _ensure_default_registry
        _ensure_default_registry()

        import config
        slots = build_strategy_slots(config.STRATEGY_SLOTS)
        # Should have 2 slots: gtaa_isa and dual_momentum_isa
        assert len(slots) == 2
        ids = {s.config.strategy_id for s in slots}
        assert "gtaa_isa" in ids
        assert "dual_momentum_isa" in ids

    def test_dispatch_with_real_strategies_and_mock_data(self, db):
        """Full dispatch with real strategies, mock data provider."""
        clear_registry()
        from app.engine.pipeline import _ensure_default_registry
        _ensure_default_registry()

        import config

        # Mock data provider that returns None (no data = errors, but no crash)
        mock_dp = MagicMock()
        mock_dp.get_daily_bars.return_value = None

        result = dispatch_orchestration(
            window_name="integration_test",
            db_path=db,
            dry_run=True,
            slot_configs=config.STRATEGY_SLOTS,
            data_provider=mock_dp,
            equity=100000.0,
        )

        summary = result.summary()
        # With no data, all tickers will error (no OHLC data available)
        # But the pipeline should not crash
        assert summary["signals_total"] == 0
        # Errors expected because data_provider returns None
        assert summary["errors"] > 0

    def test_all_disabled_returns_empty(self):
        """If all slots are disabled, returns empty list."""
        configs = [
            _make_slot_config(id="a", enabled=False),
            _make_slot_config(id="b", enabled=False),
        ]
        slots = build_strategy_slots(configs)
        assert slots == []

    def test_mixed_enabled_disabled(self):
        """Mix of enabled and disabled builds only enabled slots."""
        configs = [
            _make_slot_config(id="on1", enabled=True),
            _make_slot_config(id="off1", enabled=False),
            _make_slot_config(id="on2", enabled=True),
            _make_slot_config(id="off2", enabled=False),
        ]
        slots = build_strategy_slots(configs)
        assert len(slots) == 2
        ids = [s.config.strategy_id for s in slots]
        assert ids == ["on1", "on2"]


# ─── 6. Required fields coverage ────────────────────────────────────────

class TestRequiredFieldsCoverage:
    """Verify each required field individually raises on missing."""

    @pytest.mark.parametrize("field_name", sorted(_REQUIRED_FIELDS))
    def test_missing_individual_field_raises(self, field_name):
        """Each required field, when missing, raises ValueError."""
        cfg = _make_slot_config()
        del cfg[field_name]
        with pytest.raises(ValueError, match="missing required fields"):
            build_strategy_slots([cfg])


# ─── 7. Scheduler wiring contract ──────────────────────────────────────

class TestSchedulerWiringContract:
    """Verify that dispatch_orchestration matches the scheduler's callback API."""

    def test_dispatch_fn_callable(self):
        """dispatch_orchestration is callable (can be passed as dispatch_fn)."""
        assert callable(dispatch_orchestration)

    def test_result_has_summary_method(self, db):
        """Return value has .summary() method as scheduler expects."""
        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            from app.engine.orchestrator import OrchestrationResult
            mock_orch.return_value = OrchestrationResult(
                run_id="test", run_at="now"
            )
            result = dispatch_orchestration(
                window_name="test",
                db_path=db,
                dry_run=True,
                slot_configs=[_make_slot_config()],
            )
            summary = result.summary()
            assert isinstance(summary, dict)
            assert "signals_total" in summary
            assert "intents_created" in summary

    def test_scheduler_integration_with_dispatch(self, db):
        """DailyWorkflowScheduler can use dispatch_orchestration as callback."""
        from app.engine.scheduler import DailyWorkflowScheduler, ScheduleWindow

        scheduler = DailyWorkflowScheduler(
            dispatch_fn=dispatch_orchestration,
            schedule=[],
            db_path=db,
            dry_run=True,
        )

        # Verify it initializes without error
        status = scheduler.status()
        assert status["running"] is False
        assert status["dry_run"] is True
