"""Failure-injection tests for incidents and audit trail preservation."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import trade_db


def _init_test_db(tmp_path):
    db_path = tmp_path / "failure_incidents.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def test_failed_order_action_generates_incident_with_correlation_and_error_detail(tmp_path):
    db_path = _init_test_db(tmp_path)

    trade_db.create_order_action(
        action_id="action-fail-1",
        correlation_id="corr-fail-1",
        action_type="open_spread",
        ticker="SPY",
        spread_id="SPY:abc123",
        max_attempts=3,
        request_payload=json.dumps({"ticker": "SPY", "size": 2}),
        db_path=db_path,
    )
    trade_db.update_order_action(
        action_id="action-fail-1",
        status="failed",
        attempt=2,
        recoverable=False,
        error_code="BROKER_REJECTED",
        error_message="insufficient margin",
        result_payload=json.dumps({"broker_error": "insufficient margin"}),
        db_path=db_path,
    )

    incidents = trade_db.get_incidents(limit=25, db_path=db_path)
    order_incident = next(item for item in incidents if item["source"] == "order_action")

    assert order_incident["category"] == "FAILED"
    assert order_incident["ticker"] == "SPY"
    assert order_incident["correlation_id"] == "corr-fail-1"
    assert "insufficient margin" in order_incident["detail"]


def test_retrying_order_action_generates_retrying_incident_category(tmp_path):
    db_path = _init_test_db(tmp_path)

    trade_db.create_order_action(
        action_id="action-retry-1",
        correlation_id="corr-retry-1",
        action_type="close_spread",
        ticker="QQQ",
        spread_id="QQQ:def456",
        max_attempts=3,
        request_payload=json.dumps({"ticker": "QQQ"}),
        db_path=db_path,
    )
    trade_db.update_order_action(
        action_id="action-retry-1",
        status="retrying",
        attempt=1,
        recoverable=True,
        error_code="TRANSIENT_TIMEOUT",
        error_message="timeout waiting for broker response",
        result_payload=json.dumps({"attempt": 1}),
        db_path=db_path,
    )

    incidents = trade_db.get_incidents(limit=25, db_path=db_path)
    retry_incident = next(item for item in incidents if item["source"] == "order_action")

    assert retry_incident["category"] == "RETRYING"
    assert retry_incident["correlation_id"] == "corr-retry-1"
    assert "timeout" in retry_incident["detail"].lower()
