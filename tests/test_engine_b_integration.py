from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from tests.test_helpers import FakeFeatureStore, FakeWriteEventStore


def _route_endpoint(path: str, method: str):
    for route in server.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def _build_json_request(path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
    }
    return Request(scope, receive)


def _build_form_request(path: str, payload: dict[str, str]):
    body = urlencode(payload).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }
    return Request(scope, receive)


def _response_payload(response):
    if isinstance(response, JSONResponse):
        return response.status_code, json.loads(response.body.decode("utf-8"))
    if isinstance(response, HTMLResponse):
        return response.status_code, response.body.decode("utf-8")
    return 200, response


def test_x_intel_webhook_dual_writes_engine_b_when_cutover_inactive(monkeypatch):
    created_jobs = []
    analyzed = []
    mirrored = []

    monkeypatch.setattr(server.config, "RESEARCH_SYSTEM_ACTIVE", False)
    monkeypatch.setattr(server, "create_job", lambda **kwargs: created_jobs.append(kwargs))
    monkeypatch.setattr(server, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "analyze_intel_async", lambda submission, job_id: analyzed.append((submission, job_id)))
    monkeypatch.setattr(server, "_safe_log_event", lambda **kwargs: None)
    monkeypatch.setattr(
        server.control,
        "submit_engine_b_event",
        lambda **kwargs: mirrored.append(kwargs) or {"status": "queued", "job_id": kwargs["job_id"], "queue_depth": 0},
    )

    endpoint = _route_endpoint("/api/webhooks/x_intel", "POST")
    response = asyncio.run(
        endpoint(
            _build_json_request(
                "/api/webhooks/x_intel",
                {
                    "content": "AAPL guidance looks underappreciated after the call.",
                    "author": "@earningsdesk",
                    "url": "https://x.com/earningsdesk/status/123",
                    "tickers": ["AAPL"],
                },
            )
        )
    )
    status_code, body = _response_payload(response)

    assert status_code == 200
    assert body["ok"] is True
    assert body["message"] == "X intel queued for LLM analysis."
    assert body["research_job_id"]
    assert len(analyzed) == 1
    assert len(mirrored) == 1
    assert [job["job_type"] for job in created_jobs] == ["intel_analysis", "engine_b_intake"]
    assert mirrored[0]["source_class"] == "social_curated"
    assert mirrored[0]["source_ids"][0] == "https://x.com/earningsdesk/status/123"


def test_x_intel_webhook_routes_only_engine_b_when_cutover_active(monkeypatch):
    created_jobs = []
    analyzed = []
    mirrored = []

    monkeypatch.setattr(server.config, "RESEARCH_SYSTEM_ACTIVE", True)
    monkeypatch.setattr(server, "create_job", lambda **kwargs: created_jobs.append(kwargs))
    monkeypatch.setattr(server, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "analyze_intel_async", lambda submission, job_id: analyzed.append((submission, job_id)))
    monkeypatch.setattr(server, "_safe_log_event", lambda **kwargs: None)
    monkeypatch.setattr(
        server.control,
        "submit_engine_b_event",
        lambda **kwargs: mirrored.append(kwargs) or {"status": "queued", "job_id": kwargs["job_id"], "queue_depth": 0},
    )

    endpoint = _route_endpoint("/api/webhooks/x_intel", "POST")
    response = asyncio.run(
        endpoint(
            _build_json_request(
                "/api/webhooks/x_intel",
                {
                    "content": "MSFT revisions are still moving higher post-print.",
                    "author": "@desk",
                    "url": "https://x.com/desk/status/456",
                    "tickers": ["MSFT"],
                },
            )
        )
    )
    status_code, body = _response_payload(response)

    assert status_code == 200
    assert body["ok"] is True
    assert body["message"] == "X intel queued for Engine B research."
    assert not body["research_job_id"]
    assert not analyzed
    assert len(mirrored) == 1
    assert [job["job_type"] for job in created_jobs] == ["engine_b_intake"]


def test_sa_quant_capture_webhook_triggers_engine_b(monkeypatch):
    created_jobs = []
    mirrored = []
    stored_features = []
    store = FakeWriteEventStore()

    monkeypatch.setattr(server, "EventStore", lambda *args, **kwargs: store)
    monkeypatch.setattr(server, "FeatureStore", FakeFeatureStore)
    monkeypatch.setattr(
        server,
        "store_factor_grades",
        lambda ticker, features, feature_store, as_of=None: stored_features.append((ticker, features)) or "rec-1",
    )
    monkeypatch.setattr(server, "create_job", lambda **kwargs: created_jobs.append(kwargs))
    monkeypatch.setattr(server, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_safe_log_event", lambda **kwargs: None)
    monkeypatch.setattr(
        server.control,
        "submit_engine_b_event",
        lambda **kwargs: mirrored.append(kwargs) or {"status": "queued", "job_id": kwargs["job_id"], "queue_depth": 0},
    )

    endpoint = _route_endpoint("/api/webhooks/sa_quant_capture", "POST")
    response = asyncio.run(
        endpoint(
            _build_json_request(
                "/api/webhooks/sa_quant_capture",
                {
                    "ticker": "AAPL",
                    "url": "https://seekingalpha.com/symbol/AAPL",
                    "title": "Apple Quant Page",
                    "page_type": "symbol",
                    "rating": "Strong Buy",
                    "quant_score": "4.6",
                    "author_rating": "Bullish",
                    "wall_st_rating": "Buy",
                    "grades": {
                        "value": "B+",
                        "growth": "A",
                        "profitability": "A-",
                    },
                    "captured_at": "2026-03-09T09:00:00Z",
                },
            )
        )
    )
    status_code, body = _response_payload(response)

    assert status_code == 200
    assert body["ok"] is True
    assert body["research_job_id"]
    assert [job["job_type"] for job in created_jobs] == ["engine_b_intake"]
    assert len(store.events) == 2
    assert stored_features[0][0] == "AAPL"
    assert mirrored[0]["source_class"] == "sa_quant"
    assert "Seeking Alpha quant snapshot for AAPL" in mirrored[0]["raw_content"]


def test_intel_submit_mirrors_engine_b_when_cutover_inactive(monkeypatch):
    created_jobs = []
    analyzed = []
    mirrored = []

    monkeypatch.setattr(server.config, "RESEARCH_SYSTEM_ACTIVE", False)
    monkeypatch.setattr(server, "create_job", lambda **kwargs: created_jobs.append(kwargs))
    monkeypatch.setattr(server, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "analyze_intel_async", lambda submission, job_id: analyzed.append((submission, job_id)))
    monkeypatch.setattr(
        server.control,
        "submit_engine_b_event",
        lambda **kwargs: mirrored.append(kwargs) or {"status": "queued", "job_id": kwargs["job_id"], "queue_depth": 0},
    )

    endpoint = _route_endpoint("/api/intel/submit", "POST")
    response = asyncio.run(
        endpoint(
            _build_form_request(
                "/api/intel/submit",
                {"content": "Forward this note: NVDA demand checks still look strong."},
            )
        )
    )
    status_code, body = _response_payload(response)

    assert status_code == 200
    assert "Queued for analysis" in body
    assert len(analyzed) == 1
    assert len(mirrored) == 1
    assert [job["job_type"] for job in created_jobs] == ["intel_analysis", "engine_b_intake"]


def test_finnhub_webhook_enqueues_engine_b(monkeypatch):
    created_jobs = []
    mirrored = []

    monkeypatch.setattr(server, "create_job", lambda **kwargs: created_jobs.append(kwargs))
    monkeypatch.setattr(server, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_safe_log_event", lambda **kwargs: None)
    monkeypatch.setattr(
        server.control,
        "submit_engine_b_event",
        lambda **kwargs: mirrored.append(kwargs) or {"status": "queued", "job_id": kwargs["job_id"], "queue_depth": 0},
    )

    endpoint = _route_endpoint("/api/webhooks/finnhub", "POST")
    response = asyncio.run(
        endpoint(
            _build_json_request(
                "/api/webhooks/finnhub",
                {
                    "ticker": "AAPL",
                    "event_type": "earnings_transcript",
                    "title": "Apple transcript",
                    "content": "Management guided revenue growth above consensus and highlighted services margin expansion.",
                    "url": "https://finnhub.io/transcript/aapl",
                    "published_at": "2026-03-09T08:30:00Z",
                    "source": "finnhub",
                },
            )
        )
    )
    status_code, body = _response_payload(response)

    assert status_code == 200
    assert body["ok"] is True
    assert body["message"] == "Finnhub event queued for Engine B research."
    assert [job["job_type"] for job in created_jobs] == ["engine_b_intake"]
    assert mirrored[0]["source_class"] == "transcript"
    assert "earnings_transcript" in mirrored[0]["raw_content"]
