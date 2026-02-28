"""Integration tests for research job lifecycle and persistence via API actions."""
import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from data import trade_db


class ImmediateThread:
    """Synchronous thread test double for deterministic job execution."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)



def _bind_server_db(monkeypatch, db_path: str):
    def wrap(fn):
        def inner(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(server, "init_db", lambda: trade_db.init_db(db_path))
    monkeypatch.setattr(server, "create_job", wrap(trade_db.create_job))
    monkeypatch.setattr(server, "update_job", wrap(trade_db.update_job))
    monkeypatch.setattr(server, "get_jobs", wrap(trade_db.get_jobs))
    monkeypatch.setattr(server, "get_job", wrap(trade_db.get_job))
    monkeypatch.setattr(server, "create_calibration_run", wrap(trade_db.create_calibration_run))
    monkeypatch.setattr(server, "complete_calibration_run", wrap(trade_db.complete_calibration_run))
    monkeypatch.setattr(server, "insert_calibration_points", wrap(trade_db.insert_calibration_points))
    monkeypatch.setattr(server, "get_calibration_runs", wrap(trade_db.get_calibration_runs))
    monkeypatch.setattr(server, "get_calibration_points", wrap(trade_db.get_calibration_points))
    monkeypatch.setattr(server, "get_calibration_run", wrap(trade_db.get_calibration_run))
    monkeypatch.setattr(server, "get_option_contracts", wrap(trade_db.get_option_contracts))
    monkeypatch.setattr(server, "get_option_contract_summary", wrap(trade_db.get_option_contract_summary))



def test_discover_options_action_lifecycle_and_persistence(tmp_path, monkeypatch):
    db_path = str(tmp_path / "research_jobs_discovery.db")
    trade_db.init_db(db_path)
    _bind_server_db(monkeypatch, db_path)
    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)

    output_file = tmp_path / "options_discovery.json"

    def fake_run_discovery(search_only=False, nav_only=False, details=True, strikes=""):
        contracts = [
            {
                "index_name": "US 500",
                "epic": "OP.D.SPXWEEKLY.5200P.IP",
                "instrument_name": "US 500 Weekly Put",
                "option_type": "PUT",
                "expiry_type": "weekly",
                "expiry": "WEEKLY",
                "strike": 5200.0,
                "status": "TRADEABLE",
                "bid": 30.0,
                "offer": 32.0,
                "mid": 31.0,
                "spread": 2.0,
                "source": "search",
                "raw_payload": "{}",
            }
        ]
        persisted = trade_db.upsert_option_contracts(contracts, db_path=db_path)
        output_file.write_text("{}", encoding="utf-8")
        return {
            "ok": True,
            "message": "Discovery complete.",
            "contracts_persisted": persisted,
            "search_count": 1,
            "navigation_count": 0,
            "details_count": 0,
            "output_file": str(output_file),
        }

    monkeypatch.setattr(server.research, "run_discovery", fake_run_discovery)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/actions/discover-options",
            data={"mode": "search", "include_details": "off", "strikes": "US 500"},
        )

    assert response.status_code == 200
    jobs = trade_db.get_jobs(limit=10, db_path=db_path)
    assert jobs
    job = jobs[0]
    assert job["job_type"] == "discover_options"
    assert job["status"] == "completed"
    payload = json.loads(job["result"])
    assert payload["contracts_persisted"] == 1
    assert payload["search_count"] == 1

    contracts = trade_db.get_option_contracts(limit=10, db_path=db_path)
    assert len(contracts) == 1
    assert contracts[0]["epic"] == "OP.D.SPXWEEKLY.5200P.IP"



def test_calibrate_options_action_lifecycle_and_persistence(tmp_path, monkeypatch):
    db_path = str(tmp_path / "research_jobs_calibration.db")
    trade_db.init_db(db_path)
    _bind_server_db(monkeypatch, db_path)
    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)

    output_file = tmp_path / "calibration.json"

    def fake_run_calibration(index_filter="", verbose=False):
        raw_quotes = [
            {
                "index": "US 500",
                "ticker": "SPY",
                "strike": 5200.0,
                "otm_pct": 1.0,
                "expiry_type": "weekly",
                "dte": 8.0,
                "epic": "OP.D.SPXWEEKLY.5200P.IP",
                "ig_bid": 30.0,
                "ig_offer": 32.0,
                "ig_mid": 31.0,
                "ig_spread": 2.0,
                "ig_spread_pct": 6.45,
                "bs_price": 25.0,
                "ratio_ig_vs_bs": 1.24,
                "tradeable": True,
                "rv": 0.17,
                "iv_est": 0.2,
                "underlying": 5230.0,
            },
            {
                "index": "US 500",
                "ticker": "SPY",
                "strike": 5150.0,
                "otm_pct": 1.5,
                "expiry_type": "weekly",
                "dte": 8.0,
                "epic": "OP.D.SPXWEEKLY.5150P.IP",
                "ig_bid": 24.0,
                "ig_offer": 26.0,
                "ig_mid": 25.0,
                "ig_spread": 2.0,
                "ig_spread_pct": 8.0,
                "bs_price": 20.0,
                "ratio_ig_vs_bs": 1.25,
                "tradeable": True,
                "rv": 0.17,
                "iv_est": 0.2,
                "underlying": 5230.0,
            },
        ]
        summary = {"_overall": 1.245, "US 500": 1.245}
        output_file.write_text("{}", encoding="utf-8")
        return {
            "ok": True,
            "message": "Calibration complete.",
            "samples": len(raw_quotes),
            "summary": summary,
            "raw_quotes": raw_quotes,
            "output_file": str(output_file),
        }

    monkeypatch.setattr(server.research, "run_calibration", fake_run_calibration)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/actions/calibrate-options",
            data={"index_filter": "US 500", "verbose": "off"},
        )

    assert response.status_code == 200

    jobs = trade_db.get_jobs(limit=10, db_path=db_path)
    assert jobs
    job = jobs[0]
    assert job["job_type"] == "calibrate_options"
    assert job["status"] == "completed"
    payload = json.loads(job["result"])
    assert payload["samples"] == 2
    assert payload["stored"] == 2

    runs = trade_db.get_calibration_runs(limit=10, db_path=db_path)
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "completed"
    assert run["samples"] == 2
    assert abs(float(run["overall_ratio"]) - 1.245) < 1e-9

    points = trade_db.get_calibration_points(run_id=run["id"], limit=20, db_path=db_path)
    assert len(points) == 2
