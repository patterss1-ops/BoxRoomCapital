from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys

import httpx
from fastapi.responses import JSONResponse
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from app.signal.types import LayerId
from data import trade_db
from intelligence.event_store import EventRecord, EventStore
from intelligence.scrapers.sa_adapter import (
    SA_BROWSER_CAPTURE_EVENT_TYPE,
    SA_BROWSER_CAPTURE_SOURCE,
    SABrowserCaptureAdapter,
    parse_sa_browser_payload,
)


NOW = datetime.now(timezone.utc).replace(microsecond=0)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
OLD_ISO = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")


def _init_test_db(tmp_path):
    db_path = tmp_path / "sa_browser_capture.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def _sample_payload(captured_at: str = NOW_ISO):
    return {
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
            "momentum": "C+",
            "revisions": "B",
        },
        "captured_at": captured_at,
    }


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


class _FallbackClient:
    def __init__(self):
        self.snapshot_calls = 0
        self.score_calls = 0
        self.factor_calls = 0

    def fetch_snapshot(self, ticker: str):
        self.snapshot_calls += 1
        capture = parse_sa_browser_payload({
            "ticker": ticker,
            "rating": "Hold",
            "quant_score": 2.5,
            "captured_at": NOW_ISO,
        })
        return capture.snapshot

    def fetch_layer_score(self, ticker: str, as_of: str):
        self.score_calls += 1
        capture = parse_sa_browser_payload({
            "ticker": ticker,
            "rating": "Hold",
            "quant_score": 2.5,
            "captured_at": NOW_ISO,
        })
        from intelligence.sa_quant_client import score_sa_quant_snapshot

        return score_sa_quant_snapshot(capture.snapshot, as_of=as_of, source="fallback")

    def fetch_factor_grades(self, ticker: str):
        self.factor_calls += 1
        return {}

    def fetch_news(self, ticker: str, count: int = 20):
        return []

    def fetch_analyst_recs(self, ticker: str):
        return []

    def close(self):
        return None


def test_parse_sa_browser_payload_normalizes_quant_fields():
    capture = parse_sa_browser_payload(_sample_payload())

    assert capture.ticker == "AAPL"
    assert capture.snapshot.rating == "strong buy"
    assert capture.snapshot.quant_score_raw == 4.6
    assert capture.factor_grades["value_grade"] == "B+"
    assert capture.factor_grades["profitability_grade"] == "A-"
    assert capture.has_quant_signal is True


def test_bookmarklet_builder_preserves_https_urls():
    js = """
    // comment line should be removed
    (function () {
      var ENDPOINT = "%%ENDPOINT%%";
      fetch(ENDPOINT + "/api/webhooks/sa_intel");
    })();
    """

    href = server._build_bookmarklet_href(js, "https://example.replit.dev")

    assert href.startswith("javascript:")
    assert "https://example.replit.dev" in href
    assert 'fetch(ENDPOINT + "/api/webhooks/sa_intel")' in href


def test_sa_quant_capture_preflight_allows_seeking_alpha_origin():
    async def _run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.options(
                "/api/webhooks/sa_quant_capture",
                headers={
                    "Origin": "https://seekingalpha.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

    response = asyncio.run(_run())

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://seekingalpha.com"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_browser_capture_adapter_prefers_recent_capture(tmp_path):
    db_path = _init_test_db(tmp_path)
    payload = _sample_payload()
    store = EventStore(db_path=db_path)
    store.write_event(
        EventRecord(
            event_type=SA_BROWSER_CAPTURE_EVENT_TYPE,
            source=SA_BROWSER_CAPTURE_SOURCE,
            source_ref=payload["url"],
            retrieved_at=NOW_ISO,
            event_timestamp=NOW_ISO,
            symbol="AAPL",
            headline="SA capture",
            provenance_descriptor={"ticker": "AAPL", "url": payload["url"]},
            payload=payload,
        )
    )
    fallback = _FallbackClient()
    adapter = SABrowserCaptureAdapter(db_path=db_path, max_age_seconds=86400, fallback=fallback)

    score = adapter.fetch_layer_score("AAPL", as_of=NOW_ISO)

    assert score.layer_id == LayerId.L8_SA_QUANT
    assert score.source == "sa-browser-capture"
    assert score.details["rating"] == "strong buy"
    assert fallback.score_calls == 0
    assert adapter.fetch_factor_grades("AAPL")["value_grade"] == "B+"



def test_browser_capture_adapter_falls_back_when_capture_is_stale(tmp_path):
    db_path = _init_test_db(tmp_path)
    payload = _sample_payload(captured_at=OLD_ISO)
    store = EventStore(db_path=db_path)
    store.write_event(
        EventRecord(
            event_type=SA_BROWSER_CAPTURE_EVENT_TYPE,
            source=SA_BROWSER_CAPTURE_SOURCE,
            source_ref=payload["url"],
            retrieved_at=OLD_ISO,
            event_timestamp=OLD_ISO,
            symbol="AAPL",
            headline="Old SA capture",
            provenance_descriptor={"ticker": "AAPL", "url": payload["url"]},
            payload=payload,
        )
    )
    fallback = _FallbackClient()
    adapter = SABrowserCaptureAdapter(db_path=db_path, max_age_seconds=600, fallback=fallback)

    score = adapter.fetch_layer_score("AAPL", as_of=NOW_ISO)

    assert score.source == "fallback"
    assert fallback.score_calls == 1



def test_sa_quant_capture_webhook_stores_capture_and_signal(monkeypatch):
    recorded_events = []
    stored_features = []

    class _FakeEventStore:
        def __init__(self, *args, **kwargs):
            pass

        def write_event(self, event):
            recorded_events.append(event)
            return {"id": f"evt-{len(recorded_events)}"}

    class _FakeFeatureStore:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(server, "EventStore", _FakeEventStore)
    monkeypatch.setattr(server, "FeatureStore", _FakeFeatureStore)
    monkeypatch.setattr(
        server,
        "store_factor_grades",
        lambda ticker, features, feature_store, as_of=None: stored_features.append((ticker, features)) or "rec-1",
    )
    monkeypatch.setattr(server, "_safe_log_event", lambda **kwargs: None)

    endpoint = _route_endpoint("/api/webhooks/sa_quant_capture", "POST")
    response = asyncio.run(endpoint(_build_json_request("/api/webhooks/sa_quant_capture", _sample_payload())))

    if isinstance(response, JSONResponse):
        body = json.loads(response.body.decode("utf-8"))
        status_code = response.status_code
    else:
        body = response
        status_code = 200

    assert status_code == 200
    assert body["ok"] is True
    assert body["ticker"] == "AAPL"
    assert body["layer_score"]["source"] == "sa-browser-capture"
    assert len(recorded_events) == 2
    assert {event.event_type for event in recorded_events} == {"sa_browser_capture", "signal_layer"}
    assert stored_features[0][0] == "AAPL"
    assert stored_features[0][1]["value_grade"] > 0
