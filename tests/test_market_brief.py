"""Tests for market brief service and chart OHLCV endpoint."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from intelligence.market_brief import (
    MarketBrief,
    _parse_sections,
    _build_morning_prompt,
    _build_evening_prompt,
    generate_brief,
)


def test_market_brief_dataclass():
    brief = MarketBrief(
        brief_type="morning",
        as_of="2026-03-09T12:00:00Z",
        generated_at="2026-03-09T12:00:01Z",
        sections={"summary": "Markets are up"},
        model_used="claude-sonnet-4-20250514",
        cost_usd=0.05,
    )
    d = brief.to_dict()
    assert d["brief_type"] == "morning"
    assert d["model_used"] == "claude-sonnet-4-20250514"
    assert d["cost_usd"] == 0.05
    assert d["sections"]["summary"] == "Markets are up"


def test_parse_sections_basic():
    text = """## Overnight Summary
Asia up, Europe flat.

## Key Numbers
SPY 520, QQQ 500.

## Trading Outlook
Bullish bias."""
    sections = _parse_sections(text)
    assert "overnight_summary" in sections
    assert "key_numbers" in sections
    assert "trading_outlook" in sections
    assert "Asia up" in sections["overnight_summary"]
    assert "_full_text" in sections


def test_parse_sections_empty():
    sections = _parse_sections("")
    assert "_full_text" in sections


def test_build_morning_prompt_returns_tuple():
    system, user = _build_morning_prompt({"indices": {"SPY": {"close": 520}}}, [])
    assert "macro economist" in system.lower() or "market analyst" in system.lower()
    assert "morning" in user.lower() or "pre-market" in user.lower()


def test_build_evening_prompt_returns_tuple():
    system, user = _build_evening_prompt({"indices": {"SPY": {"close": 520}}}, [])
    assert "macro economist" in system.lower() or "market analyst" in system.lower()
    assert "end-of-day" in user.lower() or "review" in user.lower()


def test_generate_brief_without_api_key():
    """Without API keys, brief should still return with unavailable message."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "", "FINNHUB_API_KEY": ""}, clear=False):
        with patch("intelligence.market_brief._fetch_market_snapshot", return_value={"indices": {}}):
            brief = generate_brief(brief_type="morning")
            assert isinstance(brief, MarketBrief)
            assert brief.brief_type == "morning"
            assert brief.as_of


def test_generate_brief_with_mock_model_router():
    """Test brief generation with a mock model router."""
    class FakeResponse:
        raw_text = "## Session Recap\nMarkets rallied.\n## Key Numbers\nSPY +1.2%"
        parsed = None
        model_id = "test-model"
        cost_usd = 0.01

    mock_router = MagicMock()
    mock_router.call.return_value = FakeResponse()

    with patch("intelligence.market_brief._fetch_market_snapshot", return_value={}):
        with patch("intelligence.market_brief._fetch_news_headlines", return_value=[]):
            brief = generate_brief(brief_type="evening", model_router=mock_router)
            assert brief.brief_type == "evening"
            assert brief.model_used == "test-model"
            assert "session_recap" in brief.sections


# ─── Chart OHLCV endpoint tests ─────────────────────────────────────────

def test_chart_ohlcv_endpoint_exists():
    """Verify the OHLCV endpoint is registered."""
    from app.api.server import create_app
    app = create_app()
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/api/charts/ohlcv" in routes


def test_chart_ohlcv_returns_structure():
    """Test the OHLCV endpoint returns correct structure."""
    from app.api.server import create_app
    from starlette.testclient import TestClient

    app = create_app()
    client = TestClient(app)

    # Use a known ticker — this makes a real yfinance call so mock it
    import pandas as pd
    import numpy as np
    from datetime import datetime, timezone

    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    mock_df = pd.DataFrame({
        "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "High": [101.0, 102.0, 103.0, 104.0, 105.0],
        "Low": [99.0, 100.0, 101.0, 102.0, 103.0],
        "Close": [100.5, 101.5, 102.5, 103.5, 104.5],
        "Volume": [1000000, 1100000, 1200000, 1300000, 1400000],
    }, index=dates)

    with patch("yfinance.download", return_value=mock_df):
        resp = client.get("/api/charts/ohlcv?ticker=SPY&period=6mo&interval=1d")
        assert resp.status_code == 200
        data = resp.json()
        assert "candles" in data
        assert "volumes" in data
        assert len(data["candles"]) == 5
        candle = data["candles"][0]
        assert "open" in candle
        assert "high" in candle
        assert "low" in candle
        assert "close" in candle
        assert "time" in candle
