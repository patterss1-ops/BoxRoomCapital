"""Tests for C-004 promotion gate recommendation logic."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import trade_db
from fund.promotion_gate import build_promotion_gate_report, validate_lane_transition


STRATEGY_KEY = "ibs_credit_spreads"


def _init_test_db(tmp_path):
    db_path = tmp_path / "promotion_gate.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def _create_set(db_path: str, name: str, status: str = "shadow") -> dict:
    return trade_db.create_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        name=name,
        parameters_payload=json.dumps({"name": name}),
        status=status,
        db_path=db_path,
    )


def test_recommend_shadow_to_staged_when_staged_missing(tmp_path):
    db_path = _init_test_db(tmp_path)
    shadow = _create_set(db_path, "candidate-shadow", status="shadow")

    report = build_promotion_gate_report(
        strategy_key=STRATEGY_KEY,
        cooldown_hours=0,
        db_path=db_path,
    )

    assert report["recommendation"]["action"] == "PROMOTE_SHADOW_TO_STAGED"
    assert report["recommendation"]["target_set_id"] == shadow["id"]
    assert "STAGED_LIVE_MISSING" in report["recommendation"]["reason_codes"]


def test_recommend_staged_to_live_when_live_missing(tmp_path):
    db_path = _init_test_db(tmp_path)
    shadow = _create_set(db_path, "candidate-shadow", status="shadow")
    trade_db.promote_strategy_parameter_set(
        set_id=shadow["id"],
        to_status="staged_live",
        actor="test",
        acknowledgement="stage candidate",
        db_path=db_path,
    )

    report = build_promotion_gate_report(
        strategy_key=STRATEGY_KEY,
        cooldown_hours=0,
        db_path=db_path,
    )

    assert report["recommendation"]["action"] == "PROMOTE_STAGED_TO_LIVE"
    assert report["recommendation"]["target_set_id"] == shadow["id"]
    assert "LIVE_MISSING" in report["recommendation"]["reason_codes"]


def test_cooldown_can_override_recommendation(tmp_path):
    db_path = _init_test_db(tmp_path)

    first = _create_set(db_path, "candidate-1", status="shadow")
    trade_db.promote_strategy_parameter_set(
        set_id=first["id"],
        to_status="staged_live",
        actor="test",
        acknowledgement="stage 1",
        db_path=db_path,
    )
    trade_db.promote_strategy_parameter_set(
        set_id=first["id"],
        to_status="live",
        actor="test",
        acknowledgement="live 1",
        db_path=db_path,
    )

    second = _create_set(db_path, "candidate-2", status="shadow")
    trade_db.promote_strategy_parameter_set(
        set_id=second["id"],
        to_status="staged_live",
        actor="test",
        acknowledgement="stage 2",
        db_path=db_path,
    )

    report = build_promotion_gate_report(
        strategy_key=STRATEGY_KEY,
        cooldown_hours=24,
        db_path=db_path,
    )

    assert report["cooldown_active"] is True
    assert report["recommendation"]["action"] == "HOLD"
    assert "PROMOTION_COOLDOWN_ACTIVE" in report["recommendation"]["reason_codes"]


def test_validate_lane_transition_enforces_three_lane_policy():
    assert validate_lane_transition("shadow", "staged_live") == (True, [])
    assert validate_lane_transition("staged_live", "live") == (True, [])

    allowed, reasons = validate_lane_transition("shadow", "live")
    assert allowed is False
    assert reasons == ["INVALID_LANE_TRANSITION"]
