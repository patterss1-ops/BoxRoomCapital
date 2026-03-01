"""End-to-end pipeline integration tests (D-004).

Validates the complete signal → intent → dispatch lifecycle:

    Strategy.generate_signal()
        → orchestrator (signal_to_order_intent + risk gate)
        → order_intent_store (QUEUED in DB)
        → IntentDispatcher.run_once() (QUEUED → RUNNING → COMPLETED/FAILED)
        → reconciler (sync_broker_snapshot → live equity)

Each test uses a real SQLite DB, a PaperBroker, and deterministic
fake strategies — no mocks on the critical path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import pytest

from app.engine.orchestrator import OrchestrationResult, StrategySlot, run_orchestration_cycle
from app.engine.pipeline import (
    build_strategy_slots,
    clear_registry,
    dispatch_orchestration,
    register_strategy_class,
)
from broker.base import AccountInfo, BaseBroker, OrderResult, Position
from data.order_intent_store import (
    ensure_order_intent_schema,
    get_order_intents,
)
from data.trade_db import init_db
from execution.dispatcher import DispatchRunSummary, IntentDispatcher
from execution.order_intent import OrderIntentStatus
from execution.reconciler import compute_live_equity, sync_broker_snapshot
from execution.signal_adapter import StrategySlotConfig
from execution.policy.capability_policy import StrategyRequirements
from notifications import NotificationHandler
from strategies.base import BaseStrategy, Signal, SignalType


# ─── Deterministic test strategies ────────────────────────────────────────

class AlwaysBuyStrategy(BaseStrategy):
    """Emits LONG_ENTRY for every ticker on every call."""

    def __init__(self, params: dict | None = None):
        self._params = params or {}

    @property
    def name(self) -> str:
        return "always_buy"

    def generate_signal(
        self, ticker: str, df, current_position: float = 0,
        bars_in_trade: int = 0, **kwargs,
    ) -> Signal:
        if current_position > 0:
            return Signal(
                signal_type=SignalType.NONE,
                ticker=ticker,
                strategy_name=self.name,
                reason="already long",
            )
        return Signal(
            signal_type=SignalType.LONG_ENTRY,
            ticker=ticker,
            strategy_name=self.name,
            reason="always buy signal",
            size_multiplier=1.0,
        )


class AlwaysHoldStrategy(BaseStrategy):
    """Emits NONE for every ticker — no trades generated."""

    def __init__(self, params: dict | None = None):
        self._params = params or {}

    @property
    def name(self) -> str:
        return "always_hold"

    def generate_signal(
        self, ticker: str, df, current_position: float = 0,
        bars_in_trade: int = 0, **kwargs,
    ) -> Signal:
        return Signal(
            signal_type=SignalType.NONE,
            ticker=ticker,
            strategy_name=self.name,
            reason="no action",
        )


class FailingBroker(BaseBroker):
    """Broker that rejects all orders."""

    def connect(self) -> bool:
        return True

    def disconnect(self):
        pass

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            balance=10_000.0, equity=10_000.0,
            unrealised_pnl=0.0, open_positions=0, currency="GBP",
        )

    def get_positions(self) -> list[Position]:
        return []

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        return None

    def place_long(self, ticker, qty, strategy) -> OrderResult:
        return OrderResult(success=False, message="broker down")

    def place_short(self, ticker, qty, strategy) -> OrderResult:
        return OrderResult(success=False, message="broker down")

    def close_position(self, ticker, strategy) -> OrderResult:
        return OrderResult(success=False, message="broker down")


class E2EPaperBroker(BaseBroker):
    """Minimal paper broker for E2E tests — always succeeds."""

    def __init__(self):
        self._connected = False
        self._positions: list[Position] = []
        self._trade_counter = 0

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            balance=50_000.0, equity=50_000.0,
            unrealised_pnl=0.0, open_positions=len(self._positions),
            currency="GBP",
        )

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        for p in self._positions:
            if p.ticker == ticker and p.strategy == strategy:
                return p
        return None

    def place_long(self, ticker, qty, strategy) -> OrderResult:
        self._trade_counter += 1
        self._positions.append(Position(
            ticker=ticker, direction="long", size=qty,
            entry_price=100.0, entry_time=datetime.utcnow(),
            strategy=strategy,
        ))
        return OrderResult(
            success=True, order_id=f"E2E-{self._trade_counter}",
            fill_price=100.0, fill_qty=qty,
            timestamp=datetime.utcnow(),
        )

    def place_short(self, ticker, qty, strategy) -> OrderResult:
        self._trade_counter += 1
        self._positions.append(Position(
            ticker=ticker, direction="short", size=qty,
            entry_price=100.0, entry_time=datetime.utcnow(),
            strategy=strategy,
        ))
        return OrderResult(
            success=True, order_id=f"E2E-{self._trade_counter}",
            fill_price=100.0, fill_qty=qty,
            timestamp=datetime.utcnow(),
        )

    def close_position(self, ticker, strategy) -> OrderResult:
        self._trade_counter += 1
        self._positions = [
            p for p in self._positions
            if not (p.ticker == ticker and p.strategy == strategy)
        ]
        return OrderResult(
            success=True, order_id=f"E2E-{self._trade_counter}",
            fill_price=100.0, timestamp=datetime.utcnow(),
        )


# ─── Fixtures ─────────────────────────────────────────────────────────────

def _make_price_df(ticker: str, n_bars: int = 60) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame for testing."""
    base = 100.0
    dates = pd.bdate_range(end=datetime.utcnow(), periods=n_bars)
    data = {
        "Open": [base + i * 0.1 for i in range(n_bars)],
        "High": [base + i * 0.1 + 1.0 for i in range(n_bars)],
        "Low": [base + i * 0.1 - 1.0 for i in range(n_bars)],
        "Close": [base + i * 0.15 for i in range(n_bars)],
        "Volume": [1_000_000] * n_bars,
    }
    return pd.DataFrame(data, index=dates)


class FakeDataProvider:
    """Returns synthetic price data for any ticker."""

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}

    def get_daily_bars(self, ticker: str) -> pd.DataFrame:
        if ticker not in self._cache:
            self._cache[ticker] = _make_price_df(ticker)
        return self._cache[ticker]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset strategy registry for each test."""
    clear_registry()
    register_strategy_class("AlwaysBuyStrategy", AlwaysBuyStrategy)
    register_strategy_class("AlwaysHoldStrategy", AlwaysHoldStrategy)
    yield
    clear_registry()


@pytest.fixture
def db(tmp_path) -> str:
    """Create a fully initialised test database."""
    db_path = str(tmp_path / "e2e_test.db")
    init_db(db_path)
    ensure_order_intent_schema(db_path)
    return db_path


@pytest.fixture
def broker() -> E2EPaperBroker:
    return E2EPaperBroker()


@pytest.fixture
def failing_broker() -> FailingBroker:
    return FailingBroker()


@pytest.fixture
def data_provider() -> FakeDataProvider:
    return FakeDataProvider()


def _make_buy_slot_config() -> dict:
    return {
        "id": "e2e_buyer",
        "strategy_class": "AlwaysBuyStrategy",
        "strategy_version": "1.0",
        "params": {},
        "sleeve": "e2e_sleeve",
        "account_type": "PAPER",
        "broker_target": "paper",
        "tickers": ["SPY", "QQQ"],
        "base_qty": 1.0,
        "risk_tags": ["e2e_test"],
        "requirements": {"requires_spot_etf": True},
        "enabled": True,
    }


def _make_hold_slot_config() -> dict:
    return {
        "id": "e2e_holder",
        "strategy_class": "AlwaysHoldStrategy",
        "strategy_version": "1.0",
        "params": {},
        "sleeve": "e2e_sleeve",
        "account_type": "PAPER",
        "broker_target": "paper",
        "tickers": ["SPY"],
        "base_qty": 1.0,
        "risk_tags": [],
        "enabled": True,
    }


# ─── 1. Signal → Intent (Orchestrator) ───────────────────────────────────

class TestOrchestrationCreatesIntents:
    """Verify that orchestration generates signals and persists intents."""

    def test_buy_signals_create_queued_intents(self, db, data_provider):
        """AlwaysBuy strategy should produce QUEUED intents for each ticker."""
        result = dispatch_orchestration(
            window_name="e2e_test",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        assert isinstance(result, OrchestrationResult)
        assert len(result.signals) == 2  # SPY + QQQ
        assert len(result.intents_created) == 2
        assert len(result.intents_rejected) == 0

        # Verify intents are QUEUED in DB
        intents = get_order_intents(db_path=db)
        queued = [i for i in intents if i["status"] == "queued"]
        assert len(queued) == 2

        tickers = {i["instrument"] for i in queued}
        assert tickers == {"SPY", "QQQ"}

    def test_hold_strategy_creates_no_intents(self, db, data_provider):
        """AlwaysHold strategy should produce NONE signals — no intents."""
        result = dispatch_orchestration(
            window_name="e2e_test",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_hold_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        assert len(result.signals) == 1  # SPY only
        assert len(result.intents_created) == 0

    def test_dry_run_creates_shadow_trades(self, db, data_provider):
        """In dry_run mode, intents are logged but not persisted as QUEUED."""
        result = dispatch_orchestration(
            window_name="e2e_dry",
            db_path=db,
            dry_run=True,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        assert len(result.intents_created) == 2
        for intent in result.intents_created:
            assert intent.get("dry_run") is True

        # No QUEUED intents in DB
        intents = get_order_intents(db_path=db)
        assert len(intents) == 0

    def test_multiple_slots_combine_signals(self, db, data_provider):
        """Multiple strategy slots produce combined results."""
        result = dispatch_orchestration(
            window_name="e2e_multi",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config(), _make_hold_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        # AlwaysBuy: 2 signals (SPY, QQQ), AlwaysHold: 1 signal (SPY)
        assert len(result.signals) == 3
        # Only AlwaysBuy creates intents
        assert len(result.intents_created) == 2


# ─── 2. Intent → Dispatch (Dispatcher) ───────────────────────────────────

class TestDispatchConsumesIntents:
    """Verify that the dispatcher picks up QUEUED intents and submits to broker."""

    def test_full_lifecycle_queued_to_completed(self, db, data_provider, broker):
        """QUEUED intents → dispatcher → COMPLETED via paper broker."""
        # Step 1: Create QUEUED intents
        dispatch_orchestration(
            window_name="e2e_lifecycle",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        intents_before = get_order_intents(db_path=db)
        assert len(intents_before) == 2
        assert all(i["status"] == "queued" for i in intents_before)

        # Step 2: Run dispatcher with paper broker
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: broker,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once()

        assert isinstance(summary, DispatchRunSummary)
        assert summary.discovered == 2
        assert summary.completed == 2
        assert summary.failed == 0
        assert summary.errors == 0

        # Step 3: Verify intents are now COMPLETED in DB
        intents_after = get_order_intents(db_path=db)
        completed = [i for i in intents_after if i["status"] == "completed"]
        assert len(completed) == 2

    def test_broker_reject_marks_failed(self, db, data_provider, failing_broker):
        """When broker rejects an order, intent transitions to FAILED."""
        dispatch_orchestration(
            window_name="e2e_reject",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: failing_broker,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once()

        assert summary.discovered == 2
        assert summary.completed == 0
        assert summary.failed == 2

        intents = get_order_intents(db_path=db)
        failed = [i for i in intents if i["status"] == "failed"]
        assert len(failed) == 2

    def test_empty_queue_returns_zero_summary(self, db):
        """Dispatcher with no QUEUED intents returns clean empty summary."""
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: E2EPaperBroker(),
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once()

        assert summary.discovered == 0
        assert summary.processed == 0
        assert summary.completed == 0

    def test_second_dispatch_finds_nothing(self, db, data_provider, broker):
        """After first dispatch completes all intents, second dispatch is empty."""
        dispatch_orchestration(
            window_name="e2e_idempotent",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: broker,
            disconnect_after_run=False,
        )

        # First run completes both
        first = dispatcher.run_once()
        assert first.completed == 2

        # Second run finds nothing
        second = dispatcher.run_once()
        assert second.discovered == 0
        assert second.completed == 0


# ─── 3. Full Loop: Signal → Intent → Dispatch → Reconcile ────────────────

class TestFullPipelineLoop:
    """End-to-end: strategy signals → intents → dispatch → reconcile."""

    def test_signal_to_reconcile_lifecycle(self, db, data_provider):
        """Full loop: generate signal, persist intent, dispatch, reconcile."""
        e2e_broker = E2EPaperBroker()

        # Step 1: Orchestrate — creates QUEUED intents
        orch_result = dispatch_orchestration(
            window_name="e2e_full",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )
        assert len(orch_result.intents_created) == 2

        # Step 2: Dispatch — consumes QUEUED, submits to broker
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: e2e_broker,
            disconnect_after_run=False,
        )
        dispatch_summary = dispatcher.run_once()
        assert dispatch_summary.completed == 2

        # Step 3: Reconcile — sync broker state back to ledger
        recon_summary = sync_broker_snapshot(
            broker=e2e_broker,
            broker_name="paper",
            account_id="E2E-PAPER-1",
            account_type="PAPER",
            sleeve="e2e_sleeve",
            db_path=db,
        )
        assert recon_summary.positions_synced == 2
        assert recon_summary.cash_balance == 50_000.0

        # Step 4: Verify live equity is computed from ledger
        equity = compute_live_equity(default_equity=0.0, db_path=db)
        assert equity > 0  # cash + positions should be positive

    def test_dispatch_summary_dict_keys(self, db, data_provider, broker):
        """DispatchRunSummary.to_dict() has all expected keys."""
        dispatch_orchestration(
            window_name="e2e_keys",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: broker,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once()
        d = summary.to_dict()

        expected_keys = {
            "discovered", "processed", "completed",
            "retried", "failed", "errors", "claim_conflicts",
        }
        assert set(d.keys()) == expected_keys


# ─── 4. Operator Alerts ──────────────────────────────────────────────────

class TestOperatorAlerts:
    """Verify notification helpers for dispatch and pipeline events."""

    def test_dispatch_alert_formats_correctly(self):
        """dispatch_alert formats a DispatchRunSummary into a message."""
        handler = NotificationHandler()  # disabled by default (no env vars)

        summary = DispatchRunSummary(
            discovered=5, processed=5, completed=3,
            retried=1, failed=1, errors=0,
        )

        msg = handler.format_dispatch_summary(summary)
        assert "5 discovered" in msg
        assert "3 completed" in msg
        assert "1 failed" in msg

    def test_pipeline_error_alert_formats(self):
        """pipeline_error_alert includes error details."""
        handler = NotificationHandler()
        result = OrchestrationResult(
            run_id="test123", run_at="2026-03-01T21:00:00",
        )
        result.errors.append({
            "strategy_id": "gtaa_isa",
            "ticker": "SPY",
            "error": "No data available",
        })

        msg = handler.format_pipeline_errors(result)
        assert "test123" in msg
        assert "SPY" in msg
        assert "1 error" in msg

    def test_reconciliation_alert_formats(self):
        """reconciliation_alert includes sync details."""
        from execution.reconciler import ReconcileSummary
        handler = NotificationHandler()

        summary = ReconcileSummary(
            broker="paper",
            account_id="PAPER-1",
            broker_account_id="1",
            positions_synced=3,
            positions_inserted=2,
            positions_updated=1,
            positions_removed=0,
            cash_balance=45_000.0,
            net_liquidation=52_000.0,
        )

        msg = handler.format_reconciliation_summary(summary)
        assert "paper" in msg
        assert "3 positions" in msg
        assert "52,000" in msg or "52000" in msg

    def test_alert_send_returns_false_when_disabled(self):
        """send() returns False when notifications are disabled."""
        handler = NotificationHandler()
        assert handler.enabled is False
        assert handler.send("test message") is False

    def test_dispatch_alert_send_disabled(self):
        """dispatch_alert returns False when disabled."""
        handler = NotificationHandler()
        summary = DispatchRunSummary(discovered=1, processed=1, completed=1)
        result = handler.dispatch_alert(summary)
        assert result is False

    def test_pipeline_alert_with_no_errors(self):
        """pipeline_error_alert with clean result returns None message."""
        handler = NotificationHandler()
        result = OrchestrationResult(run_id="clean", run_at="now")

        msg = handler.format_pipeline_errors(result)
        assert msg is None  # No errors, no message


# ─── 5. Integration Contracts ─────────────────────────────────────────────

class TestIntegrationContracts:
    """Verify the contracts between pipeline components."""

    def test_orchestration_result_summary_keys(self, db, data_provider):
        """OrchestrationResult.summary() has required keys for scheduler."""
        result = dispatch_orchestration(
            window_name="e2e_contract",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        s = result.summary()
        required_keys = {"run_id", "run_at", "signals_total", "intents_created",
                         "intents_rejected", "errors"}
        assert required_keys.issubset(set(s.keys()))

    def test_intent_payload_has_required_dispatch_fields(self, db, data_provider):
        """Persisted intents have all fields the dispatcher needs."""
        dispatch_orchestration(
            window_name="e2e_fields",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        intents = get_order_intents(db_path=db)
        assert len(intents) > 0

        for intent in intents:
            assert intent["strategy_id"] == "e2e_buyer"
            assert intent["broker_target"] == "paper"
            assert intent["instrument"] in ("SPY", "QQQ")
            assert intent["side"] == "BUY"
            assert float(intent["qty"]) > 0
            assert intent["status"] == "queued"
            assert intent["intent_id"] is not None

    def test_dispatch_summary_is_serializable(self, db, data_provider, broker):
        """DispatchRunSummary.to_dict() is JSON-safe."""
        import json

        dispatch_orchestration(
            window_name="e2e_serial",
            db_path=db,
            dry_run=False,
            slot_configs=[_make_buy_slot_config()],
            equity=100_000.0,
            data_provider=data_provider,
        )

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda name: broker,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once()

        # Must be JSON-serializable
        serialized = json.dumps(summary.to_dict())
        parsed = json.loads(serialized)
        assert parsed["completed"] == 2

    def test_reconcile_after_empty_dispatch(self, db):
        """Reconciliation works even with no prior dispatch activity."""
        e2e_broker = E2EPaperBroker()
        recon = sync_broker_snapshot(
            broker=e2e_broker,
            broker_name="paper",
            account_id="E2E-EMPTY",
            account_type="PAPER",
            db_path=db,
        )
        assert recon.positions_synced == 0
        assert recon.cash_balance == 50_000.0

    def test_equity_fallback_with_empty_ledger(self, db):
        """compute_live_equity falls back to default when ledger is empty."""
        equity = compute_live_equity(default_equity=75_000.0, db_path=db)
        assert equity == 75_000.0
