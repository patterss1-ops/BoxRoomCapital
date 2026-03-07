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
    SA_NETWORK_CAPTURE_SOURCE,
    SA_SYMBOL_CAPTURE_EVENT_TYPE,
    SABrowserCaptureAdapter,
    normalize_sa_symbol_snapshot,
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


def _sample_sa_history_payload():
    return {
        "ticker": "MU",
        "url": "https://seekingalpha.com/symbol/MU",
        "title": "MU Micron Technology, Inc.",
        "page_type": "symbol",
        "bookmarklet_version": "network-ext-test",
        "sa_history": {
            "data": [
                {
                    "id": "[1309, Fri, 06 Mar 2026]",
                    "type": "rating",
                    "attributes": {
                        "asDate": "2026-03-06",
                        "tickerId": 1309,
                        "ratings": {
                            "authorsRating": 4.05556,
                            "sellSideRating": 4.45238,
                            "quantRating": 4.996835443037975,
                            "growthGrade": 1,
                            "momentumGrade": 1,
                            "profitabilityGrade": 1,
                            "valueGrade": 6,
                            "epsRevisionsGrade": 2,
                        },
                    },
                    "meta": {"is_locked": False},
                }
            ]
        },
    }


def _sample_relative_rankings_payload():
    return {
        "data": {
            "attributes": {
                "overallRank": 2,
                "sectorRank": 2,
                "industryRank": 1,
                "sectorName": "Information Technology",
                "primaryName": "Semiconductors",
                "totalTickers": 4262,
                "totalTickersInSector": 527,
                "totalTickersInPrimaryIndustry": 70,
            },
            "id": "1309",
            "type": "relativeRanking",
        }
    }


def _sample_primary_price_payload():
    return [
        {
            "primary_price": 397.05,
            "slug": "mu",
            "tickerId": 1309,
        }
    ]


def _sample_valuation_metrics_payload():
    return {
        "data": [
            {
                "attributes": {"meaningful": True, "value": 37.670307},
                "id": "[1309, 111]",
                "relationships": {
                    "metric_type": {"data": {"id": "111", "type": "metric_type"}},
                    "ticker": {"data": {"id": "1309", "type": "ticker"}},
                },
                "type": "metric",
            },
            {
                "attributes": {"meaningful": True, "value": 0.47828665},
                "id": "[1309, 30]",
                "relationships": {
                    "metric_type": {"data": {"id": "30", "type": "metric_type"}},
                    "ticker": {"data": {"id": "1309", "type": "ticker"}},
                },
                "type": "metric",
            },
        ],
        "included": [
            {"attributes": {"field": "pe_ratio"}, "id": "111", "type": "metric_type"},
            {"attributes": {"field": "dividend_yield"}, "id": "30", "type": "metric_type"},
        ],
    }


def _sample_metric_grades_payload():
    return {
        "data": [
            {
                "attributes": {"algo": "main_quant", "grade": 13},
                "id": '[1309, 111, "main_quant"]',
                "relationships": {
                    "metric_type": {"data": {"id": "111", "type": "metric_type"}},
                    "ticker": {"data": {"id": "1309", "type": "ticker"}},
                },
                "type": "ticker_metric_grade",
            },
            {
                "attributes": {"algo": "dividends", "grade": 13},
                "id": '[1309, 30, "dividends"]',
                "relationships": {
                    "metric_type": {"data": {"id": "30", "type": "metric_type"}},
                    "ticker": {"data": {"id": "1309", "type": "ticker"}},
                },
                "type": "ticker_metric_grade",
            },
        ],
        "included": [
            {"attributes": {"field": "pe_ratio"}, "id": "111", "type": "metric_type"},
            {"attributes": {"field": "dividend_yield"}, "id": "30", "type": "metric_type"},
        ],
    }


def _sample_sector_metrics_payload():
    return {
        "data": [
            {
                "attributes": {"meaningful": True, "value": 22.1},
                "id": "[45, 111]",
                "relationships": {
                    "metric_type": {"data": {"id": "111", "type": "metric_type"}},
                },
                "type": "metric",
            }
        ],
        "included": [
            {"attributes": {"field": "pe_ratio"}, "id": "111", "type": "metric_type"},
        ],
    }


def _sample_estimates_payload():
    return {
        "estimates": {
            "1309": {
                "eps_normalized_consensus_mean": {
                    "1": [
                        {
                            "dataitemvalue": "34.61864",
                            "effectivedate": "2026-03-05T08:33:40.000-05:00",
                            "period": {"fiscalyear": 2026, "periodtypeid": "annual"},
                        }
                    ]
                }
            }
        },
        "revisions": {},
    }


def _sample_symbol_capture_payload():
    history = _sample_sa_history_payload()
    return {
        "ticker": "MU",
        "url": "https://seekingalpha.com/symbol/MU",
        "title": "MU Micron Technology, Inc.",
        "page_type": "symbol",
        "captured_at": NOW_ISO,
        "bookmarklet_version": "sa-network-extension-0.2.0",
        "source": SA_NETWORK_CAPTURE_SOURCE,
        "summary": {
            **history,
            "captured_at": NOW_ISO,
            "source": SA_NETWORK_CAPTURE_SOURCE,
            "source_ref": "https://seekingalpha.com/symbol/MU",
            "scan_debug": {
                "requested_routes": [
                    "https://seekingalpha.com/symbol/MU/ratings/quant-ratings",
                    "https://seekingalpha.com/symbol/MU/valuation/metrics",
                ],
                "section_names": ["ratings_history", "valuation_metrics", "relative_rankings", "price"],
            },
        },
        "sections": {
            "ratings_history": {
                "response_count": 1,
                "response_urls": [
                    "https://seekingalpha.com/api/v3/symbols/MU/ratings/history",
                ],
                "routes": [
                    "https://seekingalpha.com/symbol/MU/ratings/quant-ratings",
                ],
            },
            "valuation_metrics": {
                "response_count": 2,
                "response_urls": [
                    "https://seekingalpha.com/api/v3/metrics?filter[fields]=primary_price&filter[slugs]=mu&minified=true",
                    "https://seekingalpha.com/api/v3/metrics?filter[fields]=dividend_yield,pe_ratio&filter[slugs]=mu&minified=false",
                ],
                "routes": [
                    "https://seekingalpha.com/symbol/MU/valuation/metrics",
                ],
            },
            "relative_rankings": {
                "response_count": 1,
                "response_urls": [
                    "https://seekingalpha.com/api/v3/symbols/mu/relative_rankings",
                ],
                "routes": ["https://seekingalpha.com/symbol/MU"],
            },
            "metric_grades": {
                "response_count": 1,
                "response_urls": [
                    "https://seekingalpha.com/api/v3/ticker_metric_grades?filter[fields]=dividend_yield,pe_ratio&filter[slugs]=mu&filter[algos][]=main_quant&filter[algos][]=dividends&minified=false",
                ],
                "routes": ["https://seekingalpha.com/symbol/MU/valuation/metrics"],
            },
            "sector_metrics": {
                "response_count": 1,
                "response_urls": [
                    "https://seekingalpha.com/api/v3/symbols/mu/sector_metrics?filter[fields][]=pe_ratio",
                ],
                "routes": ["https://seekingalpha.com/symbol/MU/valuation/metrics"],
            },
            "earnings_estimates": {
                "response_count": 1,
                "response_urls": [
                    "https://seekingalpha.com/api/v3/symbol_data/estimates?estimates_data_items=eps_normalized_consensus_mean&period_type=annual&relative_periods=0,1,2,3,4&ticker_ids=1309",
                ],
                "routes": ["https://seekingalpha.com/symbol/MU/earnings/earnings-revisions"],
            },
        },
        "raw_responses": [
            {
                "section": "ratings_history",
                "response_url": "https://seekingalpha.com/api/v3/symbols/MU/ratings/history",
                "frame_url": "https://seekingalpha.com/symbol/MU/ratings/quant-ratings",
                "route": "https://seekingalpha.com/symbol/MU/ratings/quant-ratings",
                "captured_at": NOW_ISO,
                "payload": history["sa_history"],
            },
            {
                "section": "relative_rankings",
                "response_url": "https://seekingalpha.com/api/v3/symbols/mu/relative_rankings",
                "frame_url": "https://seekingalpha.com/symbol/MU",
                "route": "https://seekingalpha.com/symbol/MU",
                "captured_at": NOW_ISO,
                "payload": _sample_relative_rankings_payload(),
            },
            {
                "section": "price",
                "response_url": "https://seekingalpha.com/api/v3/metrics?filter[fields]=primary_price&filter[slugs]=mu&minified=true",
                "frame_url": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "route": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "captured_at": NOW_ISO,
                "payload": _sample_primary_price_payload(),
            },
            {
                "section": "valuation_metrics",
                "response_url": "https://seekingalpha.com/api/v3/metrics?filter[fields]=dividend_yield,pe_ratio&filter[slugs]=mu&minified=false",
                "frame_url": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "route": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "captured_at": NOW_ISO,
                "payload": _sample_valuation_metrics_payload(),
            },
            {
                "section": "metric_grades",
                "response_url": "https://seekingalpha.com/api/v3/ticker_metric_grades?filter[fields]=dividend_yield,pe_ratio&filter[slugs]=mu&filter[algos][]=main_quant&filter[algos][]=dividends&minified=false",
                "frame_url": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "route": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "captured_at": NOW_ISO,
                "payload": _sample_metric_grades_payload(),
            },
            {
                "section": "sector_metrics",
                "response_url": "https://seekingalpha.com/api/v3/symbols/mu/sector_metrics?filter[fields][]=pe_ratio",
                "frame_url": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "route": "https://seekingalpha.com/symbol/MU/valuation/metrics",
                "captured_at": NOW_ISO,
                "payload": _sample_sector_metrics_payload(),
            },
            {
                "section": "earnings_estimates",
                "response_url": "https://seekingalpha.com/api/v3/symbol_data/estimates?estimates_data_items=eps_normalized_consensus_mean&period_type=annual&relative_periods=0,1,2,3,4&ticker_ids=1309",
                "frame_url": "https://seekingalpha.com/symbol/MU/earnings/earnings-revisions",
                "route": "https://seekingalpha.com/symbol/MU/earnings/earnings-revisions",
                "captured_at": NOW_ISO,
                "payload": _sample_estimates_payload(),
            },
        ],
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


def _build_get_request(path: str):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
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
    payload = _sample_payload()
    payload["tickers"] = ["AAPL", "MSFT"]
    payload["scan_debug"] = {"button_tabs": [{"label": "Ratings"}], "final": {"grades": 5}}
    capture = parse_sa_browser_payload(payload)

    assert capture.ticker == "AAPL"
    assert capture.snapshot.rating == "strong buy"
    assert capture.snapshot.quant_score_raw == 4.6
    assert capture.factor_grades["value_grade"] == "B+"
    assert capture.factor_grades["profitability_grade"] == "A-"
    assert capture.has_quant_signal is True
    assert capture.snapshot.raw_fields["tickers"] == ["AAPL", "MSFT"]
    assert capture.snapshot.raw_fields["scan_debug"]["button_tabs"][0]["label"] == "Ratings"


def test_parse_sa_browser_payload_accepts_sa_history_shape():
    capture = parse_sa_browser_payload(_sample_sa_history_payload())

    assert capture.ticker == "MU"
    assert capture.snapshot.rating == "strong buy"
    assert round(capture.snapshot.quant_score_raw or 0.0, 4) == 4.9968
    assert capture.snapshot.updated_at == "2026-03-06"
    assert capture.factor_grades["growth_grade"] == "F"
    assert capture.factor_grades["momentum_grade"] == "F"
    assert capture.factor_grades["profitability_grade"] == "F"
    assert capture.factor_grades["value_grade"] == "C"
    assert capture.factor_grades["revisions_grade"] == "D-"
    assert capture.snapshot.raw_fields["sa_authors_rating"] == "buy"
    assert capture.snapshot.raw_fields["wall_st_rating"] == "buy"
    assert capture.snapshot.raw_fields["bookmarklet_version"] == "network-ext-test"


def test_normalize_sa_symbol_snapshot_extracts_structured_sections():
    normalized = normalize_sa_symbol_snapshot(_sample_symbol_capture_payload())

    summary = normalized["summary"]
    sections = normalized["normalized_sections"]

    assert round(summary["quant_score"] or 0.0, 4) == 4.9968
    assert summary["rating"] == "strong buy"
    assert summary["sector_rank"] == 2
    assert summary["industry_rank"] == 1
    assert summary["raw_fields"]["primary_price"] == 397.05
    assert sections["relative_rankings"]["industry_name"] == "Semiconductors"
    assert sections["valuation_metrics"]["pe_ratio"] == 37.670307
    assert sections["metric_grades"]["main_quant"]["pe_ratio"]["grade"] == "A+"
    assert sections["metric_grades"]["dividends"]["dividend_yield"]["grade"] == "A+"
    assert sections["earnings_estimates"]["estimates"]["eps_normalized_consensus_mean"]["1"]["value"] == 34.61864


def test_parse_sa_browser_payload_preserves_normalized_sections():
    normalized = normalize_sa_symbol_snapshot(_sample_symbol_capture_payload())
    summary = dict(normalized["summary"])
    summary["normalized_sections"] = normalized["normalized_sections"]

    capture = parse_sa_browser_payload(summary)

    assert capture.snapshot.raw_fields["normalized_section_names"]
    assert capture.snapshot.raw_fields["normalized_sections"]["relative_rankings"]["sector_rank"] == 2
    assert capture.to_payload()["normalized_sections"]["valuation_metrics"]["pe_ratio"] == 37.670307


def test_bookmarklet_builder_preserves_https_urls():
    js = """
    // comment line should be removed
    (function () {
      var BOOKMARKLET_VERSION = "2026-03-06T12:38Z";
      var ENDPOINT = "%%ENDPOINT%%";
      fetch(ENDPOINT + "/api/webhooks/sa_intel");
    })();
    """

    href = server._build_bookmarklet_href(js, "https://example.replit.dev")

    assert href.startswith("javascript:")
    assert "https://example.replit.dev" in href
    assert 'fetch(ENDPOINT + "/api/webhooks/sa_intel")' in href


def test_extract_bookmarklet_version():
    js = 'var BOOKMARKLET_VERSION = "2026-03-06T14:44Z";'

    version = server._extract_bookmarklet_version(js)

    assert version == "2026-03-06T14:44Z"


def test_bookmarklet_js_restricts_execution_to_seeking_alpha():
    js_path = server.PROJECT_ROOT / "app" / "web" / "static" / "sa_bookmarklet.js"
    js = js_path.read_text(encoding="utf-8")

    assert 'This bookmarklet only runs on seekingalpha.com pages.' in js
    assert 'function isSeekingAlphaHost(hostname)' in js
    assert 'function sendDebugPing(stage, extra)' in js
    assert 'merged.bookmarklet_version' in js
    assert 'seekingalpha.com' in js


def test_sa_debug_ping_logs_stage(monkeypatch):
    logged = {}

    def _capture(**kwargs):
        logged.update(kwargs)

    monkeypatch.setattr(server, "_safe_log_event", _capture)

    async def _run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(
                "/api/webhooks/sa_debug_ping",
                params={
                    "stage": "pre_post",
                    "v": "2026-03-06T14:44Z",
                    "href": "https://seekingalpha.com/symbol/MU",
                    "host": "seekingalpha.com",
                    "page_type": "symbol",
                },
            )

    response = asyncio.run(_run())

    assert response.status_code == 204
    assert logged["strategy"] == "sa_debug_ping"
    assert logged["headline"] == "SA bookmarklet ping: pre_post"
    assert "2026-03-06T14:44Z" in logged["detail"]
    assert "seekingalpha.com" in logged["detail"]


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


def test_api_sa_symbol_snapshots_returns_cards(monkeypatch):
    class _FakeEventStore:
        def __init__(self, *args, **kwargs):
            pass

        def list_events(self, limit=100, event_type="", source=""):
            assert event_type == SA_SYMBOL_CAPTURE_EVENT_TYPE
            payload = _sample_symbol_capture_payload()
            payload["normalized_sections"] = normalize_sa_symbol_snapshot(payload)["normalized_sections"]
            return [
                {
                    "id": "evt-1",
                    "event_type": SA_SYMBOL_CAPTURE_EVENT_TYPE,
                    "source": SA_NETWORK_CAPTURE_SOURCE,
                    "symbol": "MU",
                    "retrieved_at": NOW_ISO,
                    "payload": payload,
                }
            ]

    monkeypatch.setattr(server, "EventStore", _FakeEventStore)

    endpoint = _route_endpoint("/api/sa/snapshots", "GET")
    body = endpoint(limit=3)

    assert body["ok"] is True
    assert body["count"] == 1
    assert body["items"][0]["ticker"] == "MU"
    assert body["items"][0]["section_names"]
    assert body["items"][0]["normalized_sections"]["relative_rankings"]["sector_rank"] == 2


def test_sa_symbol_captures_fragment_renders(monkeypatch):
    class _FakeEventStore:
        def __init__(self, *args, **kwargs):
            pass

        def list_events(self, limit=100, event_type="", source=""):
            payload = _sample_symbol_capture_payload()
            payload["normalized_sections"] = normalize_sa_symbol_snapshot(payload)["normalized_sections"]
            return [
                {
                    "id": "evt-1",
                    "event_type": SA_SYMBOL_CAPTURE_EVENT_TYPE,
                    "source": SA_NETWORK_CAPTURE_SOURCE,
                    "symbol": "MU",
                    "retrieved_at": NOW_ISO,
                    "payload": payload,
                }
            ]

    monkeypatch.setattr(server, "EventStore", _FakeEventStore)

    endpoint = _route_endpoint("/fragments/sa-symbol-captures", "GET")
    response = endpoint(_build_get_request("/fragments/sa-symbol-captures"), limit=2)
    html = response.body.decode("utf-8")

    assert "SA Symbol Snapshots" in html
    assert "MU" in html
    assert "Valuation Metrics" in html
    assert "Structured JSON" in html


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


def test_sa_symbol_capture_webhook_stores_symbol_snapshot_and_signal(monkeypatch):
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

    endpoint = _route_endpoint("/api/webhooks/sa_symbol_capture", "POST")
    response = asyncio.run(
        endpoint(_build_json_request("/api/webhooks/sa_symbol_capture", _sample_symbol_capture_payload()))
    )

    if isinstance(response, JSONResponse):
        body = json.loads(response.body.decode("utf-8"))
        status_code = response.status_code
    else:
        body = response
        status_code = 200

    assert status_code == 200
    assert body["ok"] is True
    assert body["ticker"] == "MU"
    assert body["section_count"] == 6
    assert body["normalized_section_count"] >= 5
    assert body["raw_response_count"] == 7
    assert round(body["quant_score"] or 0.0, 4) == 4.9968
    assert body["layer_score"]["source"] == "sa-browser-capture"
    assert len(recorded_events) == 3
    assert {event.event_type for event in recorded_events} == {
        SA_SYMBOL_CAPTURE_EVENT_TYPE,
        "sa_browser_capture",
        "signal_layer",
    }
    assert recorded_events[0].source == SA_NETWORK_CAPTURE_SOURCE
    assert "normalized_sections" in recorded_events[0].payload
    assert recorded_events[0].payload["normalized_sections"]["relative_rankings"]["sector_rank"] == 2
    assert recorded_events[1].source == SA_NETWORK_CAPTURE_SOURCE
    assert recorded_events[1].payload["normalized_sections"]["relative_rankings"]["sector_rank"] == 2
    assert recorded_events[1].payload["raw_fields"]["normalized_sections"]["valuation_metrics"]["pe_ratio"] == 37.670307
    assert recorded_events[2].payload["details"]["primary_price"] == 397.05
    assert "ratings_history" in recorded_events[2].payload["details"]["section_names"]
    assert stored_features[0][0] == "MU"
    assert stored_features[0][1]["growth_grade"] > 0
