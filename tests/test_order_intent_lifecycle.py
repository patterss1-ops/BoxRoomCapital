"""Integration tests for order intent lifecycle + audit envelope persistence."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import trade_db
from data.order_intent_store import (
    create_order_intent_envelope,
    get_order_intent,
    get_order_intent_attempts,
    get_order_intent_transitions,
    transition_order_intent,
)
from execution.order_intent import OrderIntent


def _init_test_db(tmp_path):
    db_path = tmp_path / "order_intent_lifecycle.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def _build_intent() -> OrderIntent:
    return OrderIntent(
        strategy_id="ibs_credit_spreads",
        strategy_version="v4.2",
        sleeve="options_income",
        account_type="SPREADBET",
        broker_target="ig",
        instrument="SPY",
        side="SELL",
        qty=2,
        order_type="LIMIT",
        risk_tags=["max_loss:strict", "vol:high"],
        metadata={"strategy_param_set": "ibs-shadow-candidate"},
    )


def test_order_intent_lifecycle_persists_retries_and_payloads(tmp_path):
    db_path = _init_test_db(tmp_path)

    created = create_order_intent_envelope(
        intent=_build_intent(),
        action_type="open_spread",
        max_attempts=3,
        request_payload={"ticker": "SPY", "size": 2},
        actor="system",
        db_path=db_path,
    )
    intent_id = created["intent_id"]
    correlation_id = created["correlation_id"]
    action_id = created["action_id"]

    assert created["status"] == "queued"
    assert created["latest_attempt"] == 0
    assert correlation_id

    transition_order_intent(
        intent_id=intent_id,
        status="running",
        attempt=1,
        actor="system",
        request_payload={"deal_reference": "ref-a1"},
        db_path=db_path,
    )
    transition_order_intent(
        intent_id=intent_id,
        status="retrying",
        attempt=1,
        actor="system",
        response_payload={"detail": "timeout from broker"},
        error_code="TRANSIENT_TIMEOUT",
        error_message="request timed out",
        recoverable=True,
        db_path=db_path,
    )
    transition_order_intent(
        intent_id=intent_id,
        status="running",
        attempt=2,
        actor="system",
        request_payload={"deal_reference": "ref-a2"},
        db_path=db_path,
    )
    transition_order_intent(
        intent_id=intent_id,
        status="completed",
        attempt=2,
        actor="operator",
        response_payload={"short_deal_id": "DIAAA", "long_deal_id": "DIBBB"},
        db_path=db_path,
    )

    final_intent = get_order_intent(intent_id, db_path=db_path)
    assert final_intent is not None
    assert final_intent["status"] == "completed"
    assert final_intent["actor"] == "operator"
    assert final_intent["latest_attempt"] == 2
    assert final_intent["correlation_id"] == correlation_id

    attempts = get_order_intent_attempts(intent_id, db_path=db_path)
    assert [a["attempt"] for a in attempts] == [0, 1, 2]
    attempt_1 = attempts[1]
    assert attempt_1["status"] == "retrying"
    assert attempt_1["error_code"] == "TRANSIENT_TIMEOUT"
    assert attempt_1["error_message"] == "request timed out"
    assert attempt_1["request_payload"]["deal_reference"] == "ref-a1"
    assert attempt_1["response_payload"]["detail"] == "timeout from broker"

    attempt_2 = attempts[2]
    assert attempt_2["status"] == "completed"
    assert attempt_2["request_payload"]["deal_reference"] == "ref-a2"
    assert attempt_2["response_payload"]["short_deal_id"] == "DIAAA"

    transitions = get_order_intent_transitions(intent_id, db_path=db_path)
    assert len(transitions) == 5
    assert transitions[0]["from_status"] is None
    assert transitions[0]["to_status"] == "queued"
    assert {t["actor"] for t in transitions} == {"system", "operator"}
    assert all(t["transition_at"] for t in transitions)

    action_rows = trade_db.get_order_actions(limit=20, db_path=db_path)
    action = next(a for a in action_rows if a["id"] == action_id)
    assert action["correlation_id"] == correlation_id
    assert action["status"] == "completed"
    assert action["attempt"] == 2
    result_payload = json.loads(action["result_payload"])
    assert result_payload["short_deal_id"] == "DIAAA"


def test_failed_order_intent_captures_broker_error_payload(tmp_path):
    db_path = _init_test_db(tmp_path)

    created = create_order_intent_envelope(
        intent=_build_intent(),
        action_type="close_spread",
        max_attempts=1,
        request_payload={"spread_id": "SPY:abc123"},
        actor="operator",
        db_path=db_path,
    )
    intent_id = created["intent_id"]
    action_id = created["action_id"]

    transition_order_intent(
        intent_id=intent_id,
        status="running",
        attempt=1,
        actor="system",
        request_payload={"deal_reference": "close-a1"},
        db_path=db_path,
    )
    transition_order_intent(
        intent_id=intent_id,
        status="failed",
        attempt=1,
        actor="system",
        response_payload={"broker_error": "insufficient margin"},
        error_code="BROKER_REJECTED",
        error_message="margin check failed",
        db_path=db_path,
    )

    intent = get_order_intent(intent_id, db_path=db_path)
    assert intent is not None
    assert intent["status"] == "failed"
    assert intent["latest_attempt"] == 1

    attempts = get_order_intent_attempts(intent_id, db_path=db_path)
    assert len(attempts) == 2
    attempt_1 = attempts[1]
    assert attempt_1["status"] == "failed"
    assert attempt_1["error_code"] == "BROKER_REJECTED"
    assert attempt_1["response_payload"]["broker_error"] == "insufficient margin"

    transitions = get_order_intent_transitions(intent_id, db_path=db_path)
    last = transitions[-1]
    assert last["to_status"] == "failed"
    assert last["error_code"] == "BROKER_REJECTED"
    assert last["error_message"] == "margin check failed"
    assert last["response_payload"]["broker_error"] == "insufficient margin"

    action_rows = trade_db.get_order_actions(limit=20, db_path=db_path)
    action = next(a for a in action_rows if a["id"] == action_id)
    assert action["status"] == "failed"
    assert action["error_code"] == "BROKER_REJECTED"
    assert action["error_message"] == "margin check failed"
    assert json.loads(action["result_payload"])["broker_error"] == "insufficient margin"
