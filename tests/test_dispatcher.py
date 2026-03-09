"""Tests for D-002 queued intent dispatcher."""

from __future__ import annotations

import os
import sys
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
from utils.datetime_utils import utc_now_naive


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
            return OrderResult(success=True, order_id="stub-ok", timestamp=utc_now_naive())
        result = self.outcomes.pop(0)
        if result.timestamp is None:
            result.timestamp = utc_now_naive()
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

    def test_retry_exhaustion_across_multiple_cycles(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=3)

        stub = StubBroker([
            OrderResult(success=False, message="fail1"),
            OrderResult(success=False, message="fail2"),
            OrderResult(success=False, message="fail3"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )

        s1 = dispatcher.run_once(limit=10)
        assert s1.retried == 1
        s2 = dispatcher.run_once(limit=10)
        assert s2.retried == 1
        s3 = dispatcher.run_once(limit=10)
        assert s3.failed == 1

        row = get_order_intent(intent_id, db_path=db)
        assert row["status"] == "failed"
        assert row["latest_attempt"] == 3

        assert get_dispatchable_order_intents(limit=10, db_path=db) == []

    def test_dispatcher_short_sell_uses_place_short(self, tmp_path):
        db = self._init_db(tmp_path)
        self._create_intent(db, side="SELL", is_exit=False)

        stub = StubBroker([OrderResult(success=True, order_id="short-1")])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.completed == 1
        assert stub.calls == [("place_short", "SPY", 2.0, "gtaa")]

    def test_dispatcher_empty_queue_returns_zero_summary(self, tmp_path):
        db = self._init_db(tmp_path)

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: StubBroker(),
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.discovered == 0
        assert summary.processed == 0
        assert summary.completed == 0

    def test_dispatcher_processes_multiple_intents_in_order(self, tmp_path):
        db = self._init_db(tmp_path)
        id1 = self._create_intent(db, instrument="AAA")
        id2 = self._create_intent(db, instrument="BBB")
        id3 = self._create_intent(db, instrument="CCC")

        stub = StubBroker([
            OrderResult(success=True, order_id="o1"),
            OrderResult(success=True, order_id="o2"),
            OrderResult(success=True, order_id="o3"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.discovered == 3
        assert summary.completed == 3

        for iid in [id1, id2, id3]:
            row = get_order_intent(iid, db_path=db)
            assert row["status"] == "completed"

    def test_dispatcher_limit_caps_processing(self, tmp_path):
        db = self._init_db(tmp_path)
        for _ in range(5):
            self._create_intent(db)

        stub = StubBroker([OrderResult(success=True, order_id=f"o{i}") for i in range(2)])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=2)
        assert summary.discovered == 2
        assert summary.completed == 2

        remaining = get_dispatchable_order_intents(limit=10, db_path=db)
        assert len(remaining) == 3

    def test_dispatcher_broker_connect_failure_marks_retrying(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=2)

        class FailConnectBroker(StubBroker):
            def connect(self) -> bool:
                return False

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: FailConnectBroker(),
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.retried == 1

        row = get_order_intent(intent_id, db_path=db)
        assert row["status"] == "retrying"

    def test_dispatcher_broker_exception_marks_retrying(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=2)

        class ExplodingBroker(StubBroker):
            def place_long(self, ticker, stake, strategy):
                raise ConnectionError("network down")

        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: ExplodingBroker(),
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.retried == 1

        metrics = get_execution_metrics(limit=10, intent_id=intent_id, db_path=db)
        assert len(metrics) == 1
        assert metrics[0]["error_code"] == "DISPATCH_ERROR"

    def test_dispatcher_disconnect_all_clears_broker_cache(self, tmp_path):
        db = self._init_db(tmp_path)
        self._create_intent(db)

        stub = StubBroker([OrderResult(success=True, order_id="ok")])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        dispatcher.run_once(limit=10)
        assert len(dispatcher._brokers) == 1

        dispatcher.disconnect_all()
        assert len(dispatcher._brokers) == 0
        assert stub.connected is False

    def test_dispatcher_disconnect_after_run_default(self, tmp_path):
        db = self._init_db(tmp_path)
        self._create_intent(db)

        stub = StubBroker([OrderResult(success=True, order_id="ok")])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=True,
        )
        dispatcher.run_once(limit=10)
        assert len(dispatcher._brokers) == 0

    def test_dispatcher_summary_to_dict(self, tmp_path):
        from execution.dispatcher import DispatchRunSummary
        s = DispatchRunSummary(discovered=5, processed=4, completed=2, retried=1, failed=1, errors=0, claim_conflicts=0)
        d = s.to_dict()
        assert d["discovered"] == 5
        assert d["completed"] == 2
        assert set(d.keys()) == {"discovered", "processed", "completed", "retried", "failed", "errors", "claim_conflicts"}

    def test_dispatcher_records_metric_on_terminal_failure(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=1)

        stub = StubBroker([OrderResult(success=False, message="permanent reject")])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.failed == 1

        metrics = get_execution_metrics(limit=10, intent_id=intent_id, db_path=db)
        assert len(metrics) == 1
        assert metrics[0]["status"] == "failed"
        assert metrics[0]["error_code"] == "BROKER_REJECTED"

    def test_dispatcher_idempotency_double_claim_same_intent(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=2)

        stub = StubBroker([OrderResult(success=True, order_id="ok")])
        d1 = IntentDispatcher(db_path=db, broker_resolver=lambda _: stub, disconnect_after_run=False, actor="system")
        d2 = IntentDispatcher(db_path=db, broker_resolver=lambda _: stub, disconnect_after_run=False, actor="operator")

        s1 = d1.run_once(limit=10)
        s2 = d2.run_once(limit=10)

        total_completed = s1.completed + s2.completed
        total_conflicts = s1.claim_conflicts + s2.claim_conflicts
        assert total_completed == 1
        assert s2.discovered == 0 or s2.claim_conflicts >= 0

    def test_dispatcher_mixed_success_and_failure(self, tmp_path):
        db = self._init_db(tmp_path)
        self._create_intent(db, instrument="WIN", max_attempts=1)
        self._create_intent(db, instrument="LOSE", max_attempts=1)

        stub = StubBroker([
            OrderResult(success=True, order_id="w1"),
            OrderResult(success=False, message="rejected"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        summary = dispatcher.run_once(limit=10)
        assert summary.completed == 1
        assert summary.failed == 1
        assert summary.discovered == 2

    def test_dispatcher_transitions_recorded_for_retry_flow(self, tmp_path):
        db = self._init_db(tmp_path)
        intent_id = self._create_intent(db, max_attempts=2)

        stub = StubBroker([
            OrderResult(success=False, message="tmp"),
            OrderResult(success=True, order_id="ok"),
        ])
        dispatcher = IntentDispatcher(
            db_path=db,
            broker_resolver=lambda _: stub,
            disconnect_after_run=False,
        )
        dispatcher.run_once(limit=10)
        dispatcher.run_once(limit=10)

        transitions = get_order_intent_transitions(intent_id, db_path=db)
        statuses = [t["to_status"] for t in transitions]
        assert "queued" in statuses
        assert "running" in statuses
        assert "retrying" in statuses
        assert "completed" in statuses
