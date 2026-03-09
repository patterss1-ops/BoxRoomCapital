from __future__ import annotations

import json

from starlette.requests import Request

from app.api import server


def _route_endpoint(path: str, method: str):
    for route in server.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def _build_request(path: str):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
    }
    return Request(scope, receive)


def test_build_job_detail_summary_for_signal_shadow_report():
    parsed_result = {
        "run_id": "shadow-1",
        "run_at": "2026-03-09T12:00:00Z",
        "summary": {
            "tickers_total": 2,
            "tickers_scored": 1,
            "tickers_blocked_missing_required_layers": 0,
            "tickers_blocked_stale_layers": 1,
        },
        "results": [
            {
                "ticker": "AAPL",
                "status": "scored",
                "action": "auto_execute_buy",
                "final_score": 82.4,
                "weighted_score": 80.0,
                "layer_count": 9,
                "missing_required_layers": [],
                "vetoes": ["research_blocking_objections"],
                "layer_scores": {"l9_research": 35.0, "l1_pead": 90.0},
                "freshness": {"warning_layers": [], "stale_layers": []},
                "notes": [],
            },
            {
                "ticker": "MSFT",
                "status": "blocked_stale_layers",
                "action": "no_action",
                "final_score": 40.0,
                "weighted_score": 41.0,
                "layer_count": 4,
                "missing_required_layers": [],
                "vetoes": [],
                "layer_scores": {"l1_pead": 45.0},
                "freshness": {"warning_layers": [], "stale_layers": ["l6_news_sentiment"]},
                "notes": [],
            },
        ],
    }

    summary = server._build_job_detail_summary("signal_shadow_run", parsed_result)

    assert summary is not None
    assert summary["kind"] == "signal_shadow_run"
    assert summary["tickers_scored"] == 1
    assert summary["research_overlay"]["tickers_with_research_layer"] == 1
    assert summary["research_overlay"]["tickers_blocked_by_research"] == 1
    assert summary["top_candidates"][0]["ticker"] == "AAPL"
    assert summary["top_candidates"][0]["research_layer_score"] == 35.0


def test_build_job_detail_summary_for_tier1_report_includes_l9_job():
    parsed_result = {
        "run_id": "tier1-1",
        "run_at": "2026-03-09T12:05:00Z",
        "research_summary": {
            "tickers_success": 2,
            "tickers_failed": 1,
            "tickers_skipped": 3,
        },
        "layer_jobs": {
            "l9_research": {
                "status": "completed",
                "detail": "success=2, failed=1, skipped=3",
                "job_id": "job-l9",
            }
        },
        "ranked_candidates": [{"ticker": "AAPL"}],
        "shadow_report": {
            "run_id": "shadow-inner",
            "run_at": "2026-03-09T12:05:00Z",
            "summary": {
                "tickers_total": 3,
                "tickers_scored": 2,
                "tickers_blocked_missing_required_layers": 0,
                "tickers_blocked_stale_layers": 0,
            },
            "results": [
                {
                    "ticker": "AAPL",
                    "status": "scored",
                    "action": "auto_execute_buy",
                    "final_score": 84.0,
                    "weighted_score": 82.0,
                    "layer_count": 9,
                    "missing_required_layers": [],
                    "vetoes": [],
                    "layer_scores": {"l9_research": 71.0},
                    "freshness": {"warning_layers": [], "stale_layers": []},
                    "notes": [],
                }
            ],
        },
    }

    summary = server._build_job_detail_summary("signal_tier1_shadow_run", parsed_result)

    assert summary is not None
    assert summary["kind"] == "signal_tier1_shadow_run"
    assert summary["ranked_count"] == 1
    assert summary["research_job"]["status"] == "completed"
    assert summary["research_job"]["tickers_success"] == 2
    assert summary["research_overlay"]["tickers_with_research_layer"] == 1


def test_job_detail_fragment_renders_signal_summary(monkeypatch):
    job = {
        "id": "job-1",
        "job_type": "signal_tier1_shadow_run",
        "status": "completed",
        "updated_at": "2026-03-09T12:05:00Z",
        "detail": "scored=2/3, ranked=1",
        "result": json.dumps(
            {
                "run_id": "tier1-1",
                "run_at": "2026-03-09T12:05:00Z",
                "research_summary": {
                    "tickers_success": 2,
                    "tickers_failed": 1,
                    "tickers_skipped": 3,
                },
                "layer_jobs": {
                    "l9_research": {
                        "status": "completed",
                        "detail": "success=2, failed=1, skipped=3",
                        "job_id": "job-l9",
                    }
                },
                "ranked_candidates": [{"ticker": "AAPL"}],
                "shadow_report": {
                    "run_id": "shadow-inner",
                    "run_at": "2026-03-09T12:05:00Z",
                    "summary": {
                        "tickers_total": 3,
                        "tickers_scored": 2,
                        "tickers_blocked_missing_required_layers": 0,
                        "tickers_blocked_stale_layers": 0,
                    },
                    "results": [
                        {
                            "ticker": "AAPL",
                            "status": "scored",
                            "action": "auto_execute_buy",
                            "final_score": 84.0,
                            "weighted_score": 82.0,
                            "layer_count": 9,
                            "missing_required_layers": [],
                            "vetoes": ["research_blocking_objections"],
                            "layer_scores": {"l9_research": 35.0},
                            "freshness": {"warning_layers": [], "stale_layers": []},
                            "notes": [],
                        }
                    ],
                },
            }
        ),
        "error": "",
    }

    monkeypatch.setattr(server, "get_job", lambda job_id: job if job_id == "job-1" else None)
    monkeypatch.setattr(server, "get_jobs", lambda limit=40: [job])
    monkeypatch.setattr(
        server.control,
        "pipeline_status",
        lambda: {"engine_b": {"running": True, "status": "running", "queue_depth": 3}},
    )
    monkeypatch.setattr(server.config, "RESEARCH_SYSTEM_ACTIVE", True)

    endpoint = _route_endpoint("/fragments/job-detail", "GET")
    response = endpoint(_build_request("/fragments/job-detail"), job_id="job-1")
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Research Routing" in body
    assert "Engine B Primary" in body
    assert "Queue 3" in body
    assert "Tier-1 Shadow Summary" in body
    assert "Research Overlay" in body
    assert "L9 Research Job" in body
    assert "Top Candidates" in body
    assert "research_blocking_objections" in body


def test_jobs_template_renders_view_button_for_signal_jobs():
    scope = {"type": "http", "method": "GET", "path": "/fragments/jobs", "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    template = server.TEMPLATES.get_template("_jobs.html")
    html = template.render(
        {
            "request": request,
            "research_route_label": "Council Primary + Engine B Mirror",
            "engine_b_state": {"running": True, "status": "running", "queue_depth": 2},
            "jobs": [
                {
                    "id": "job-1",
                    "created_at": "2026-03-09T12:00:00Z",
                    "job_type": "signal_tier1_shadow_run",
                    "status": "completed",
                    "detail": "scored=2/3",
                    "result": "",
                    "error": "",
                }
            ],
        }
    )

    assert "/fragments/job-detail?job_id=job-1" in html
    assert "Research Routing" in html
    assert "Queue 2" in html
    assert "View" in html
