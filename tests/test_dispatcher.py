"""Tests for D-002 queued intent dispatcher."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from broker.base import AccountInfo, BaseBroker, OrderResult, Position
from data import trade_db
from data.order_intent_store import (
    claim_order_intent_for_dispatch,
    create_order_intent_envelope,
    get_execution_metrics,
    get_dispatchable_order_intents,
    get_order_intent,
    get_order_intent_attempts,
    get_order_intent_transitions,
)
from execution.dispatcher import IntentDispatcher
from execution.order_intent import OrderIntent


class StubBroker(BaseBroker):
    """Minimal broker stub for dispatcher lifecycle tests."""

    def __init__(self, outcomes: Optional[list[OrderResult]] = None):
        self.outcomes = list(outcomes or [OrderResult(success=True, order_id="stub-1")])
        self.calls: list[tuple] = []
        self.connected = False

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(balance=10_000.0, equity=10_000.0, unrealised_pnl=0.0, open_positions=0)

    def get_positions(self) -> list[Position]:
        return []

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        return None

    def _next_outcome(self) -> OrderResult:
        if not self.outcomes:
            return OrderResult(success=True, order_id="stub-ok", timestamp=datetime.utcnow())
        result = self.outcomes.pop(0)
        if result.timestamp is None:
            result.timestamp = datetime.utcnow()
        return result

    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        self.calls.append(("place_long", ticker, stake_per_point, strategy))
        return self._next_outcome()

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        self.calls.append(("place_short", ticker, stake_per_point, strategy))
        return self._next_outcome()

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        self.calls.append(("close_position", ticker, strategy))
        return self._next_outcome()


class TestIntentDispatcher:
    def _init_db(self, tmp_path) -> str:
        db_path = tmp_path / "dispatcher.db"
        trade_db.init_db(str(db_path))
        return str(db_path)

    def _create_intent(
        self,
        db_path: str,
        *,
        broker_target: str = "paper",
        side: str = "BUY",
        max_attempts: int = 2,
        is_exit: bool = False,
        qty: float = 2.0,
        instrument: str = "SPY",
        metadata: Optional[dict] = None,
    ) -> str:
        intent_metadata = {"is_exit": is_exit}
        if metadata:
            intent_metadata.update(metadata)
        intent = OrderIntent(
            strategy_id="gtaa",
            strategy_version="v1",
            sleeve="core",
            account_type="PAPER",
            broker_target=broker_target,
            instrument=instrument,
            side=side,
            qty=qty,
            order_type="MARKET",
            metadata=intent_metadata,
        )
        created = create_order_intent_envelope(
            intent=intent,
            action_type="orchestrator_cycle",
            max_attempts=max_attempts,
            actor="system",
            db_path=db_path,
        )
        return str(created["intent_id"])

    def test_dispatcher_completes_queued_intent(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, side="BUY", max_attempts=2)

        stub = StubBroker([
            OrderResult(success=True, order_id="ord-1", fill_qty=2.0, fill_price=501.25),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        summary = dispatcher.run_once(limit=10)

        assert summary.discovered == 1
        assert summary.processed == 1
        assert summary.completed == 1
        assert summary.retried == 0
        assert summary.failed == 0
        assert summary.claim_conflicts == 0
        assert stub.calls == [("place_long", "SPY", 2.0, "gtaa")]

        row = get_order_intent(intent_id, db_path=db)
        assert row is not None
        assert row["status"] == "completed"
        assert row["latest_attempt"] == 1

        attempts = get_order_intent_attempts(intent_id, db_path=db)
        assert [a["attempt"] for a in attempts] == [0, 1]
        assert attempts[1]["status"] == "completed"
        assert attempts[1]["response_payload"]["order_id"] == "ord-1"

        transitions = get_order_intent_transitions(intent_id, db_path=db)
        assert [t["to_status"] for t in transitions] == ["queued", "running", "completed"]

    def test_dispatcher_retry_then_complete(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, side="SELL", max_attempts=3)

        stub = StubBroker([
            OrderResult(success=False, message="temporary broker reject"),
            OrderResult(success=True, order_id="ord-2"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        first = dispatcher.run_once(limit=10)
        assert first.retried == 1
        assert first.completed == 0

        retry_row = get_order_intent(intent_id, db_path=db)
        assert retry_row is not None
        assert retry_row["status"] == "retrying"
        assert retry_row["latest_attempt"] == 1

        second = dispatcher.run_once(limit=10)
        assert second.completed == 1
        assert second.retried == 0

        final = get_order_intent(intent_id, db_path=db)
        assert final is not None
        assert final["status"] == "completed"
        assert final["latest_attempt"] == 2

    def test_dispatcher_marks_failed_when_attempt_budget_exhausted(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=1)

        stub = StubBroker([
            OrderResult(success=False, message="hard reject"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        summary = dispatcher.run_once(limit=10)

        assert summary.failed == 1
        assert summary.retried == 0
        assert summary.claim_conflicts == 0

        row = get_order_intent(intent_id, db_path=db)
        assert row is not None
        assert row["status"] == "failed"
        assert row["latest_attempt"] == 1

        assert get_dispatchable_order_intents(limit=10, db_path=db) == []

    def test_dispatcher_exit_intent_uses_close_position(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, is_exit=True, side="SELL")

        stub = StubBroker([
            OrderResult(success=True, order_id="close-1"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        summary = dispatcher.run_once(limit=10)

        assert summary.completed == 1
        assert stub.calls == [("close_position", "SPY", "gtaa")]

        row = get_order_intent(intent_id, db_path=db)
        assert row is not None
        assert row["status"] == "completed"

    def test_dispatcher_unsupported_broker_becomes_retrying(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, broker_target="unknown_broker", max_attempts=2)

        dispatcher = IntentDispatcher(db_path=db)

        summary = dispatcher.run_once(limit=10)

        assert summary.retried == 1
        assert summary.failed == 0

        row = get_order_intent(intent_id, db_path=db)
        assert row is not None
        assert row["status"] == "retrying"
        assert row["latest_attempt"] == 1

    def test_atomic_claim_allows_only_one_dispatcher_winner(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=2)

        first = claim_order_intent_for_dispatch(
            intent_id=intent_id,
            attempt=1,
            actor="system",
            request_payload={"worker": "a"},
            db_path=db,
        )
        second = claim_order_intent_for_dispatch(
            intent_id=intent_id,
            attempt=1,
            actor="system",
            request_payload={"worker": "b"},
            db_path=db,
        )

        assert first is True
        assert second is False

        row = get_order_intent(intent_id, db_path=db)
        assert row is not None
        assert row["status"] == "running"
        assert row["latest_attempt"] == 1

        transitions = get_order_intent_transitions(intent_id, db_path=db)
        assert [t["to_status"] for t in transitions] == ["queued", "running"]

    def test_persist_failure_after_submit_does_not_leave_running(self, tmp_path, monkeypatch):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, side="BUY", max_attempts=2)

        stub = StubBroker([
            OrderResult(success=True, order_id="ord-ok"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        import execution.dispatcher as dispatcher_module

        real_transition = dispatcher_module.transition_order_intent
        state = {"raised": False}

        def flaky_transition(*args, **kwargs):
            if kwargs.get("status") == "completed" and not state["raised"]:
                state["raised"] = True
                raise RuntimeError("simulated completion write failure")
            return real_transition(*args, **kwargs)

        monkeypatch.setattr(dispatcher_module, "transition_order_intent", flaky_transition)

        summary = dispatcher.run_once(limit=10)

        assert summary.completed == 0
        assert summary.failed == 1
        assert summary.errors >= 1

        row = get_order_intent(intent_id, db_path=db)
        assert row is not None
        assert row["status"] == "failed"
        assert row["latest_attempt"] == 1

    def test_dispatchable_query_orders_oldest_first(self, tmp_path):
        db = self._init_db(tmp_path)
        first = self._create_intent(db, instrument="AAA")
        second = self._create_intent(db, instrument="BBB")

        items = get_dispatchable_order_intents(limit=10, db_path=db)
        ids = [x["intent_id"] for x in items]

        assert ids[0] == first
        assert ids[1] == second

    def test_dispatcher_records_execution_metric_on_completed_fill(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(
            db,
            side="BUY",
            qty=2.0,
            metadata={"reference_price": 100.0},
        )

        stub = StubBroker([
            OrderResult(success=True, order_id="ord-telemetry", fill_qty=2.0, fill_price=101.0),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        summary = dispatcher.run_once(limit=10)
        assert summary.completed == 1

        metrics = get_execution_metrics(limit=10, intent_id=intent_id, db_path=db)
        assert len(metrics) == 1
        metric = metrics[0]
        assert metric["status"] == "completed"
        assert metric["qty_requested"] == 2.0
        assert metric["qty_filled"] == 2.0
        assert metric["fill_price"] == 101.0
        assert metric["reference_price"] == 100.0
        assert metric["slippage_bps"] == 100.0
        assert metric["error_code"] is None
        assert metric["dispatch_latency_ms"] is not None
        assert metric["notional_filled"] == 202.0

    def test_dispatcher_records_execution_metric_on_retrying_reject(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=3)

        stub = StubBroker([
            OrderResult(success=False, message="venue unavailable"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        summary = dispatcher.run_once(limit=10)
        assert summary.retried == 1

        metrics = get_execution_metrics(limit=10, intent_id=intent_id, db_path=db)
        assert len(metrics) == 1
        metric = metrics[0]
        assert metric["status"] == "retrying"
        assert metric["error_code"] == "BROKER_REJECTED"
        assert metric["error_message"] == "venue unavailable"
        assert metric["qty_filled"] == 0.0
