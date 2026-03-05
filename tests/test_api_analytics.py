"""API and fragment tests for portfolio analytics surface (O-005)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.api.server import create_app
from starlette.requests import Request


def _sample_payload() -> dict:
    return {
        "ok": True,
        "generated_at": "2026-03-03T20:00:00Z",
        "days": 90,
        "points": 90,
        "latest_nav": 125000.0,
        "latest_daily_return_pct": 0.45,
        "metrics": {
            "total_return_pct": 12.3,
            "annualised_return_pct": 8.1,
            "annualised_volatility_pct": 6.2,
            "sharpe_ratio": 1.1,
            "sortino_ratio": 1.6,
            "calmar_ratio": 0.9,
            "max_drawdown_pct": -9.3,
            "win_rate_pct": 54.0,
            "profit_factor": 1.35,
        },
        "drawdowns": [],
        "rolling": {
            "window": 21,
            "dates": [],
            "rolling_return_pct": [],
            "rolling_volatility_pct": [],
            "rolling_sharpe": [],
        },
        "series": [],
    }


def _route_endpoint(app, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def test_api_portfolio_analytics_endpoint_uses_builder():
    with patch("app.api.server.build_portfolio_analytics_payload", return_value=_sample_payload()) as mock_builder:
        with patch("app.api.server.init_db"):
            app = create_app()
        endpoint = _route_endpoint(app, "/api/analytics/portfolio")
        payload = endpoint(days=120)

    assert payload["ok"] is True
    mock_builder.assert_called_once_with(days=120)


def test_portfolio_analytics_fragment_renders():
    with patch("app.api.server.build_portfolio_analytics_payload", return_value=_sample_payload()):
        with patch("app.api.server.init_db"):
            app = create_app()
        endpoint = _route_endpoint(app, "/fragments/portfolio-analytics")
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/fragments/portfolio-analytics",
                "headers": [],
                "query_string": b"",
            }
        )
        response = endpoint(request=request, days=90)
        html = response.template.render(response.context)

    assert "Analytics" in html
    assert "Sharpe" in html
    assert "Return" in html


def test_overview_page_includes_portfolio_analytics_panel():
    template_path = Path("app/web/templates/overview.html")
    html = template_path.read_text(encoding="utf-8")
    assert 'id="portfolio-analytics-panel"' in html
    assert 'hx-get="/fragments/portfolio-analytics"' in html
