"""Tests for strategy parameter set versioning and promotion workflow."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import trade_db


STRATEGY_KEY = "ibs_credit_spreads"


def _init_test_db(tmp_path):
    db_path = tmp_path / "strategy_params.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def test_strategy_parameter_set_versioning(tmp_path):
    db_path = _init_test_db(tmp_path)

    first = trade_db.create_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        name="baseline-shadow",
        parameters_payload=json.dumps({"min_credit_pct": 3.0}),
        status="shadow",
        db_path=db_path,
    )
    second = trade_db.create_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        name="baseline-shadow-v2",
        parameters_payload=json.dumps({"min_credit_pct": 2.8}),
        status="shadow",
        db_path=db_path,
    )

    assert first["version"] == 1
    assert second["version"] == 2

    rows = trade_db.get_strategy_parameter_sets(
        strategy_key=STRATEGY_KEY,
        limit=10,
        db_path=db_path,
    )
    assert rows[0]["id"] == second["id"]
    assert rows[1]["id"] == first["id"]

    active_shadow = trade_db.get_active_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        status="shadow",
        db_path=db_path,
    )
    assert active_shadow is not None
    assert active_shadow["id"] == second["id"]


def test_strategy_parameter_set_promotion_archives_previous_same_target(tmp_path):
    db_path = _init_test_db(tmp_path)

    set_a = trade_db.create_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        name="candidate-a",
        parameters_payload=json.dumps({"expiry_days": 10}),
        status="shadow",
        db_path=db_path,
    )
    set_b = trade_db.create_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        name="candidate-b",
        parameters_payload=json.dumps({"expiry_days": 8}),
        status="shadow",
        db_path=db_path,
    )

    promote_a = trade_db.promote_strategy_parameter_set(
        set_id=set_a["id"],
        to_status="staged_live",
        actor="test",
        acknowledgement="Stage candidate A",
        db_path=db_path,
    )
    assert promote_a["ok"] is True

    promote_b = trade_db.promote_strategy_parameter_set(
        set_id=set_b["id"],
        to_status="staged_live",
        actor="test",
        acknowledgement="Stage candidate B",
        db_path=db_path,
    )
    assert promote_b["ok"] is True

    refreshed_a = trade_db.get_strategy_parameter_set(set_a["id"], db_path=db_path)
    refreshed_b = trade_db.get_strategy_parameter_set(set_b["id"], db_path=db_path)
    assert refreshed_a is not None and refreshed_a["status"] == "archived"
    assert refreshed_b is not None and refreshed_b["status"] == "staged_live"

    promote_live = trade_db.promote_strategy_parameter_set(
        set_id=set_b["id"],
        to_status="live",
        actor="test",
        acknowledgement="Promote staged set to live",
        db_path=db_path,
    )
    assert promote_live["ok"] is True

    active_live = trade_db.get_active_strategy_parameter_set(
        strategy_key=STRATEGY_KEY,
        status="live",
        db_path=db_path,
    )
    assert active_live is not None
    assert active_live["id"] == set_b["id"]

    promotions = trade_db.get_strategy_promotions(strategy_key=STRATEGY_KEY, limit=10, db_path=db_path)
    assert len(promotions) == 3
    assert promotions[0]["to_status"] == "live"


def test_calibration_points_filters(tmp_path):
    db_path = _init_test_db(tmp_path)
    run_id = "run-filter-1"
    trade_db.create_calibration_run(run_id=run_id, scope="all", status="running", db_path=db_path)
    trade_db.insert_calibration_points(
        run_id=run_id,
        points=[
            {
                "index": "US 500",
                "ticker": "SPY",
                "expiry_type": "weekly",
                "strike": 5200,
                "ratio_ig_vs_bs": 1.2,
                "ig_mid": 35.0,
                "bs_price": 29.2,
            },
            {
                "index": "Germany 40",
                "ticker": "EWG",
                "expiry_type": "monthly",
                "strike": 22000,
                "ratio_ig_vs_bs": 1.1,
                "ig_mid": 70.0,
                "bs_price": 63.6,
            },
        ],
        db_path=db_path,
    )

    rows = trade_db.get_calibration_points(
        run_id=run_id,
        index_name="US",
        expiry_type="weekly",
        strike_min=5100,
        strike_max=5300,
        db_path=db_path,
    )
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SPY"
