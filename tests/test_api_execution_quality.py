"""
API surface tests for execution quality endpoints (G-002).

Validates the /api/execution-quality JSON endpoint and
/fragments/execution-quality HTML fragment are wired correctly.
"""

import pytest
from unittest.mock import patch

from app.api.server import create_app
from tests.asgi_client import ASGITestClient


@pytest.fixture
def client():
    """Create a test client with mocked dependencies."""
    with patch("app.api.server.init_db"):
        app = create_app()
    with ASGITestClient(app) as client:
        yield client


# ─── JSON API endpoint ────────────────────────────────────────────────────


class TestExecutionQualityAPI:
    """Tests for /api/execution-quality endpoint."""

    def test_default_response(self, client):
        """API returns valid JSON with all expected keys."""
        with patch(
            "app.api.routes.research.get_execution_quality_payload",
            return_value={
                "window_label": "30d",
                "window_start": "2026-02-01T00:00:00",
                "window_end": "2026-03-02T00:00:00",
                "verdict": "no_data",
                "generated_at": "2026-03-02T18:00:00",
                "fills": {
                    "total_attempts": 0,
                    "completed": 0,
                    "failed": 0,
                    "retrying": 0,
                    "fill_rate_pct": 0.0,
                    "reject_rate_pct": 0.0,
                    "partial_fill_rate_pct": 0.0,
                    "avg_fill_ratio": 0.0,
                },
                "slippage": {
                    "sample_count": 0,
                    "mean_bps": None,
                    "median_bps": None,
                    "p5_bps": None,
                    "p95_bps": None,
                    "max_bps": None,
                    "min_bps": None,
                    "total_slippage_cost": 0.0,
                },
                "latency": {
                    "sample_count": 0,
                    "mean_ms": None,
                    "median_ms": None,
                    "p50_ms": None,
                    "p95_ms": None,
                    "max_ms": None,
                },
                "by_broker": [],
                "by_strategy": [],
            },
        ):
            response = client.get("/api/execution-quality")
        assert response.status_code == 200
        data = response.json()
        assert data["verdict"] == "no_data"
        assert "fills" in data
        assert "slippage" in data
        assert "latency" in data

    def test_custom_days_parameter(self, client):
        """API accepts days query parameter."""
        with patch(
            "app.api.routes.research.get_execution_quality_payload",
            return_value={"verdict": "healthy", "fills": {}, "slippage": {}, "latency": {}, "by_broker": [], "by_strategy": [], "window_label": "7d", "window_start": "", "window_end": "", "generated_at": ""},
        ) as mock_fn:
            response = client.get("/api/execution-quality?days=7")
        assert response.status_code == 200
        mock_fn.assert_called_once_with(days=7)


# ─── HTML fragment endpoint ───────────────────────────────────────────────


class TestExecutionQualityFragment:
    """Tests for /fragments/execution-quality HTML endpoint."""

    def test_fragment_returns_html(self, client):
        """Fragment endpoint returns HTML containing expected elements."""
        with patch(
            "app.api.routes.fragments.get_execution_quality_payload",
            return_value={
                "window_label": "30d",
                "window_start": "2026-02-01T00:00:00",
                "window_end": "2026-03-02T00:00:00",
                "verdict": "healthy",
                "generated_at": "2026-03-02T18:00:00",
                "fills": {
                    "total_attempts": 100,
                    "completed": 95,
                    "failed": 3,
                    "retrying": 2,
                    "fill_rate_pct": 95.0,
                    "reject_rate_pct": 3.0,
                    "partial_fill_rate_pct": 5.0,
                    "avg_fill_ratio": 0.98,
                },
                "slippage": {
                    "sample_count": 95,
                    "mean_bps": 8.5,
                    "median_bps": 7.0,
                    "p5_bps": 1.0,
                    "p95_bps": 22.0,
                    "max_bps": 35.0,
                    "min_bps": -2.0,
                    "total_slippage_cost": 425.0,
                },
                "latency": {
                    "sample_count": 100,
                    "mean_ms": 150.0,
                    "median_ms": 130.0,
                    "p50_ms": 130.0,
                    "p95_ms": 320.0,
                    "max_ms": 500.0,
                },
                "by_broker": [
                    {
                        "broker": "ibkr",
                        "total_attempts": 80,
                        "fill_rate_pct": 97.5,
                        "reject_rate_pct": 1.25,
                        "mean_slippage_bps": 7.0,
                        "mean_latency_ms": 140.0,
                    },
                ],
                "by_strategy": [],
            },
        ):
            response = client.get("/fragments/execution-quality")
        assert response.status_code == 200
        html = response.text
        assert "Execution Quality" in html
        assert "HEALTHY" in html
        assert "95.0" in html  # fill rate

    def test_fragment_no_data(self, client):
        """Fragment handles no_data verdict gracefully."""
        with patch(
            "app.api.routes.fragments.get_execution_quality_payload",
            return_value={
                "verdict": "no_data",
                "generated_at": "",
                "window_label": "30d",
                "window_start": "",
                "window_end": "",
                "fills": {
                    "total_attempts": 0, "completed": 0, "failed": 0, "retrying": 0,
                    "fill_rate_pct": 0, "reject_rate_pct": 0, "partial_fill_rate_pct": 0, "avg_fill_ratio": 0,
                },
                "slippage": {
                    "sample_count": 0, "mean_bps": None, "median_bps": None,
                    "p5_bps": None, "p95_bps": None, "max_bps": None, "min_bps": None,
                    "total_slippage_cost": 0,
                },
                "latency": {
                    "sample_count": 0, "mean_ms": None, "median_ms": None,
                    "p50_ms": None, "p95_ms": None, "max_ms": None,
                },
                "by_broker": [],
                "by_strategy": [],
            },
        ):
            response = client.get("/fragments/execution-quality")
        assert response.status_code == 200
        assert "No execution data" in response.text
