"""Tests for E-007 Signal Engine shadow API + workflow surface."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from app.engine import signal_shadow
from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from data import trade_db
from intelligence.event_store import EventRecord, EventStore
from intelligence.jobs import signal_layer_jobs
from tests.asgi_client import ASGITestClient


class ImmediateThread:
    """Synchronous thread test double for deterministic API action tests."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def _bind_job_db(monkeypatch, db_path: str):
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


def _layer(
    ticker: str,
    layer_id: LayerId,
    score: float,
    *,
    as_of: str = "2026-03-02T09:00:00Z",
    source: str = "unit-test",
) -> LayerScore:
    return LayerScore(
        layer_id=layer_id,
        ticker=ticker,
        score=score,
        as_of=as_of,
        source=source,
        provenance_ref=f"{source}:{ticker}:{layer_id.value}",
        confidence=0.85,
        details={"seeded": True},
    )


def _write_layer_event(
    store: EventStore,
    layer_score: LayerScore,
    *,
    retrieved_at: str,
    run_ref: str,
):
    store.write_event(
        EventRecord(
            event_type="signal_layer",
            source=layer_score.source,
            source_ref=f"{layer_score.provenance_ref}:{run_ref}",
            retrieved_at=retrieved_at,
            event_timestamp=layer_score.as_of,
            symbol=layer_score.ticker,
            headline=f"{layer_score.layer_id.value} score",
            detail=f"score={layer_score.score}",
            confidence=layer_score.confidence,
            provenance_descriptor={
                "layer_id": layer_score.layer_id.value,
                "ticker": layer_score.ticker,
                "run_ref": run_ref,
            },
            payload=layer_score.to_dict(),
        )
    )


def test_api_signal_shadow_snapshot_route(monkeypatch):
    payload = {
        "ok": True,
        "state": "idle",
        "has_report": False,
        "report": None,
        "event_stats": {"tickers_with_layers": 0, "layer_coverage": {}, "latest_layer_as_of": None},
    }
    monkeypatch.setattr(server, "get_signal_shadow_report", lambda: payload)

    with ASGITestClient(server.app) as client:
        response = client.get("/api/signal-shadow")

    assert response.status_code == 200
    assert response.json() == payload


def test_signal_shadow_action_persists_job_lifecycle(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_shadow_jobs.db")
    trade_db.init_db(db_path)
    _bind_job_db(monkeypatch, db_path)
    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        server,
        "run_signal_shadow_cycle",
        lambda: {
            "run_id": "shadow123",
            "run_at": "2026-03-02T10:00:00Z",
            "summary": {"tickers_total": 2, "tickers_scored": 1},
            "results": [],
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.post("/api/actions/signal-shadow-run")

    assert response.status_code == 200
    assert "Queued signal shadow run" in response.text

    jobs = trade_db.get_jobs(limit=5, db_path=db_path)
    assert jobs
    job = jobs[0]
    assert job["job_type"] == "signal_shadow_run"
    assert job["status"] == "completed"
    assert "scored=1/2" in (job["detail"] or "")
    assert "shadow123" in (job["result"] or "")


def test_signal_tier1_action_persists_job_lifecycle(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_tier1_jobs.db")
    trade_db.init_db(db_path)
    _bind_job_db(monkeypatch, db_path)
    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        server,
        "run_tier1_shadow_jobs",
        lambda: {
            "run_id": "tier1abc",
            "shadow_report": {"summary": {"tickers_total": 3, "tickers_scored": 2}},
            "ranked_candidates": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "freshness_diagnostics": {"blocked_stale_layers": 1},
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.post("/api/actions/signal-tier1-run")

    assert response.status_code == 200
    assert "Queued tier-1 shadow run" in response.text

    jobs = trade_db.get_jobs(limit=5, db_path=db_path)
    assert jobs
    job = jobs[0]
    assert job["job_type"] == "signal_tier1_shadow_run"
    assert job["status"] == "completed"
    assert "scored=2/3" in (job["detail"] or "")
    assert "ranked=2" in (job["detail"] or "")
    assert "tier1abc" in (job["result"] or "")


def test_api_signal_shadow_enriches_with_rankings(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_signal_shadow_report",
        lambda: {
            "ok": True,
            "state": "ready",
            "has_report": True,
            "event_stats": {"tickers_with_layers": 1, "layer_coverage": {}, "latest_layer_as_of": "2026-03-02T10:00:00Z"},
            "report": {
                "run_id": "r1",
                "run_at": "2026-03-02T10:00:00Z",
                "summary": {"tickers_total": 1, "tickers_scored": 1},
                "results": [
                    {
                        "ticker": "AAPL",
                        "status": "scored",
                        "action": "auto_execute_buy",
                        "final_score": 81.2,
                        "weighted_score": 78.0,
                        "layer_count": 8,
                        "missing_required_layers": [],
                        "vetoes": ["research_blocking_objections"],
                        "layer_scores": {"l9_research": 35.0},
                        "freshness": {"warning_layers": [], "stale_layers": []},
                        "notes": ["quality_penalty_pct=0.0"],
                    }
                ],
            },
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/api/signal-shadow")

    assert response.status_code == 200
    body = response.json()
    assert body["has_report"] is True
    assert body["report"]["ranked_candidates"][0]["ticker"] == "AAPL"
    assert body["report"]["ranked_candidates"][0]["research_layer_score"] == 35.0
    assert body["freshness_diagnostics"]["tickers_with_stale"] == 0
    assert body["research_overlay_diagnostics"]["tickers_with_research_layer"] == 1


def test_signal_engine_fragment_renders(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_signal_shadow_report",
        lambda: {
            "ok": True,
            "state": "ready",
            "has_report": True,
            "event_stats": {
                "tickers_with_layers": 2,
                "layer_coverage": {"l1_pead": 2, "l8_sa_quant": 1},
                "latest_layer_as_of": "2026-03-02T10:00:00Z",
            },
            "report": {
                "run_id": "abcd1234",
                "run_at": "2026-03-02T10:01:00Z",
                "summary": {
                    "tickers_total": 2,
                    "tickers_scored": 1,
                    "action_counts": {"auto_execute_buy": 1, "flag_for_review": 0, "short_candidate": 0, "no_action": 0},
                },
                "results": [
                    {
                        "ticker": "AAPL",
                        "status": "scored",
                        "final_score": 82.4,
                        "action": "auto_execute_buy",
                        "layer_count": 2,
                        "missing_required_layers": ["l2_insider", "l4_analyst_revisions"],
                        "vetoes": ["research_blocking_objections"],
                        "layer_scores": {"l9_research": 35.0},
                    }
                ],
                "ranked_candidates": [
                    {
                        "rank": 1,
                        "ticker": "AAPL",
                        "action": "auto_execute_buy",
                        "final_score": 82.4,
                        "rank_score": 82.4,
                        "warning_layers": [],
                        "stale_layers": [],
                    }
                ],
            },
            "freshness_diagnostics": {"tickers_with_warnings": 0, "tickers_with_stale": 0},
        },
    )

    with ASGITestClient(server.app) as client:
        response = client.get("/fragments/signal-engine")

    assert response.status_code == 200
    assert "Signal Engine Shadow Run" in response.text
    assert "abcd1234" in response.text
    assert "AAPL" in response.text
    assert "auto_execute_buy" in response.text
    assert "Ranked Candidates" in response.text
    assert "Freshness diagnostics" in response.text
    assert "Research overlay" in response.text
    assert "research_blocking_objections" in response.text
    assert "35.00" in response.text


def test_run_signal_shadow_cycle_uses_latest_layer_events(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_shadow_cycle.db")
    trade_db.init_db(db_path)
    store = EventStore(db_path=db_path)

    # Isolate ticker universe to seeded events for deterministic assertions.
    monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

    old_l1 = _layer("AAPL", LayerId.L1_PEAD, 20.0, source="pead")
    new_l1 = _layer("AAPL", LayerId.L1_PEAD, 90.0, source="pead")
    l8 = _layer("AAPL", LayerId.L8_SA_QUANT, 88.0, source="sa-quant")
    msft_l1 = _layer("MSFT", LayerId.L1_PEAD, 55.0, source="pead")

    _write_layer_event(store, old_l1, retrieved_at="2026-03-02T08:00:00Z", run_ref="old")
    _write_layer_event(store, new_l1, retrieved_at="2026-03-02T09:00:00Z", run_ref="new")
    _write_layer_event(store, l8, retrieved_at="2026-03-02T09:05:00Z", run_ref="sa")
    _write_layer_event(store, msft_l1, retrieved_at="2026-03-02T09:10:00Z", run_ref="msft")

    report = signal_shadow.run_signal_shadow_cycle(
        db_path=db_path,
        required_layers=(LayerId.L1_PEAD, LayerId.L8_SA_QUANT),
        min_layers_for_score=2,
        now_fn=lambda: "2026-03-02T10:00:00Z",
    )

    assert report["summary"]["tickers_total"] == 2
    assert report["summary"]["tickers_scored"] == 1
    assert report["summary"]["tickers_insufficient_layers"] == 1

    aapl = next(row for row in report["results"] if row["ticker"] == "AAPL")
    msft = next(row for row in report["results"] if row["ticker"] == "MSFT")
    assert aapl["status"] == "scored"
    assert aapl["action"] == "auto_execute_buy"
    assert aapl["layer_scores"]["l1_pead"] == 90.0
    assert msft["status"] == "insufficient_layers"

    snapshot = signal_shadow.get_signal_shadow_report(db_path=db_path)
    assert snapshot["ok"] is True
    assert snapshot["has_report"] is True
    assert snapshot["report"]["run_id"] == report["run_id"]
    assert snapshot["event_stats"]["layer_coverage"]["l1_pead"] == 2


def test_run_signal_shadow_cycle_blocks_missing_required_layers(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_shadow_missing_required.db")
    trade_db.init_db(db_path)
    store = EventStore(db_path=db_path)
    monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

    l1 = _layer("AAPL", LayerId.L1_PEAD, 76.0, source="pead")
    l8 = _layer("AAPL", LayerId.L8_SA_QUANT, 74.0, source="sa-quant")
    _write_layer_event(store, l1, retrieved_at="2026-03-02T09:00:00Z", run_ref="l1")
    _write_layer_event(store, l8, retrieved_at="2026-03-02T09:02:00Z", run_ref="l8")

    report = signal_shadow.run_signal_shadow_cycle(
        db_path=db_path,
        required_layers=(LayerId.L1_PEAD, LayerId.L2_INSIDER, LayerId.L8_SA_QUANT),
        min_layers_for_score=2,
        enforce_required_layers=True,
        now_fn=lambda: "2026-03-02T10:00:00Z",
    )

    assert report["summary"]["tickers_total"] == 1
    assert report["summary"]["tickers_scored"] == 0
    assert report["summary"]["tickers_blocked_missing_required_layers"] == 1
    row = report["results"][0]
    assert row["status"] == "blocked_missing_required_layers"
    assert row["action"] == "no_action"
    assert "missing_required_layers" in row["vetoes"]


def test_run_signal_shadow_cycle_blocks_stale_layers(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_shadow_stale_layers.db")
    trade_db.init_db(db_path)
    store = EventStore(db_path=db_path)
    monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

    fresh_l1 = _layer("AAPL", LayerId.L1_PEAD, 82.0, as_of="2026-03-02T09:00:00Z", source="pead")
    stale_news = _layer(
        "AAPL",
        LayerId.L6_NEWS_SENTIMENT,
        78.0,
        as_of="2026-02-26T09:00:00Z",
        source="news",
    )
    _write_layer_event(store, fresh_l1, retrieved_at="2026-03-02T09:01:00Z", run_ref="l1")
    _write_layer_event(store, stale_news, retrieved_at="2026-03-02T09:02:00Z", run_ref="l6")

    report = signal_shadow.run_signal_shadow_cycle(
        db_path=db_path,
        required_layers=(LayerId.L1_PEAD, LayerId.L6_NEWS_SENTIMENT),
        min_layers_for_score=2,
        now_fn=lambda: "2026-03-02T10:00:00Z",
    )

    assert report["summary"]["tickers_total"] == 1
    assert report["summary"]["tickers_scored"] == 0
    assert report["summary"]["tickers_blocked_stale_layers"] == 1
    row = report["results"][0]
    assert row["status"] == "blocked_stale_layers"
    assert row["action"] == "no_action"
    assert "stale_layer_data" in row["vetoes"]
    assert row["freshness"]["stale_layers"] == ["l6_news_sentiment"]


def test_build_ranked_candidates_orders_actions():
    report = {
        "results": [
            {
                "ticker": "AAPL",
                "status": "scored",
                "action": "auto_execute_buy",
                "final_score": 83.0,
                "weighted_score": 80.0,
                "layer_count": 8,
                "missing_required_layers": [],
                "freshness": {"warning_layers": ["l2_insider"], "stale_layers": []},
                "notes": ["quality_penalty_pct=2.5"],
            },
            {
                "ticker": "TSLA",
                "status": "scored",
                "action": "short_candidate",
                "final_score": 18.0,
                "weighted_score": 20.0,
                "layer_count": 8,
                "missing_required_layers": [],
                "freshness": {"warning_layers": [], "stale_layers": ["l6_news_sentiment"]},
                "notes": ["quality_penalty_pct=15.0"],
            },
            {
                "ticker": "MSFT",
                "status": "scored",
                "action": "no_action",
                "final_score": 51.0,
                "weighted_score": 50.0,
                "layer_count": 8,
                "missing_required_layers": [],
                "freshness": {"warning_layers": [], "stale_layers": []},
                "notes": ["quality_penalty_pct=0.0"],
            },
        ]
    }

    ranked = signal_layer_jobs.build_ranked_candidates(report, limit=10)

    assert [row["ticker"] for row in ranked] == ["AAPL", "TSLA"]
    assert ranked[0]["rank"] == 1
    assert ranked[1]["rank"] == 2
