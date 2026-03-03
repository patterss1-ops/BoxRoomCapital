"""Tests for H-003 metrics and health endpoints."""

from __future__ import annotations

from app.api import server
from app.metrics import build_api_health_payload, render_prometheus_metrics


def _lookup_endpoint(path: str):
    app = server.create_app()
    for route in app.routes:
        if getattr(route, "path", None) == path and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"GET endpoint not found for path: {path}")


def test_build_api_health_payload_contains_checks(tmp_path):
    db = tmp_path / "metrics.db"
    from data.trade_db import init_db

    init_db(str(db))
    payload = build_api_health_payload(db_path=str(db))
    assert payload["status"] in {"ok", "degraded"}
    assert "generated_at" in payload
    assert "checks" in payload
    assert "db" in payload["checks"]
    assert "execution_quality" in payload["checks"]


def test_render_prometheus_metrics_contains_expected_series():
    text = render_prometheus_metrics(
        {
            "window_label": "14d",
            "signal_scoring_total_24h": 12.0,
            "ai_gate_rejections_total_24h": 3.0,
            "execution_fill_rate_pct": 91.5,
            "execution_reject_rate_pct": 5.5,
            "execution_mean_latency_ms": 120.0,
            "execution_mean_slippage_bps": 8.2,
        }
    )
    assert "brc_signal_scoring_total_24h 12.0" in text
    assert "brc_ai_gate_rejections_total_24h 3.0" in text
    assert 'brc_execution_fill_rate_pct{window="14d"} 91.5' in text
    assert 'brc_execution_mean_latency_ms{window="14d"} 120.0' in text


def test_api_health_endpoint_uses_metrics_builder(monkeypatch):
    expected = {
        "status": "ok",
        "generated_at": "2026-03-03T00:00:00Z",
        "checks": {"db": {"status": "ok", "detail": "up"}},
    }
    monkeypatch.setattr(server, "build_api_health_payload", lambda: expected)

    endpoint = _lookup_endpoint("/api/health")
    payload = endpoint()
    assert payload == expected


def test_api_metrics_endpoint_returns_plaintext(monkeypatch):
    expected_text = "# HELP test\n# TYPE test gauge\ntest 1\n"
    monkeypatch.setattr(server, "build_prometheus_metrics_payload", lambda days=14: expected_text)

    endpoint = _lookup_endpoint("/api/metrics")
    resp = endpoint(days=30)
    assert resp.body.decode("utf-8") == expected_text
    assert "text/plain" in (resp.media_type or "")
