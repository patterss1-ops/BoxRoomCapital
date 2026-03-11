"""Tests for TradingView news headlines client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from intelligence.tradingview_news_client import (
    TradingViewHeadline,
    TradingViewNewsClient,
    TradingViewNewsConfig,
)


class TestSymbolResolution:
    def test_known_tickers(self):
        assert TradingViewNewsClient.resolve_symbol("SPY") == "AMEX:SPY"
        assert TradingViewNewsClient.resolve_symbol("AAPL") == "NASDAQ:AAPL"
        assert TradingViewNewsClient.resolve_symbol("QQQ") == "NASDAQ:QQQ"

    def test_unknown_ticker_defaults_to_nasdaq(self):
        assert TradingViewNewsClient.resolve_symbol("ZZZZ") == "NASDAQ:ZZZZ"

    def test_passthrough_if_already_qualified(self):
        assert TradingViewNewsClient.resolve_symbol("NYSE:BA") == "NYSE:BA"

    def test_case_insensitive(self):
        assert TradingViewNewsClient.resolve_symbol("spy") == "AMEX:SPY"


class TestFetchHeadlines:
    def _make_response(self, items):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = items
        return resp

    def test_parses_list_response(self):
        items = [
            {"id": "abc123", "title": "Markets rally", "provider": "Reuters", "published": 1741564800},
            {"id": "def456", "title": "Fed holds rates", "provider": "Bloomberg", "published": 1741564900},
        ]
        session = MagicMock()
        session.get.return_value = self._make_response(items)

        client = TradingViewNewsClient(session=session, sleep_fn=lambda _: None)
        headlines = client.fetch_headlines("SPY")

        assert len(headlines) == 2
        assert headlines[0].headline_id == "abc123"
        assert headlines[0].title == "Markets rally"
        assert headlines[0].provider == "Reuters"
        assert headlines[0].ticker == "SPY"

    def test_parses_dict_with_items_key(self):
        data = {"items": [{"id": "x1", "title": "News item", "provider": "AP", "published": 1741564800}]}
        session = MagicMock()
        session.get.return_value = self._make_response(data)

        client = TradingViewNewsClient(session=session, sleep_fn=lambda _: None)
        headlines = client.fetch_headlines("AAPL")

        assert len(headlines) == 1
        assert headlines[0].ticker == "AAPL"

    def test_skips_empty_titles(self):
        items = [
            {"id": "a", "title": "", "provider": "X", "published": 1741564800},
            {"id": "b", "title": "Valid headline", "provider": "Y", "published": 1741564800},
        ]
        session = MagicMock()
        session.get.return_value = self._make_response(items)

        client = TradingViewNewsClient(session=session, sleep_fn=lambda _: None)
        headlines = client.fetch_headlines("SPY")

        assert len(headlines) == 1

    def test_retries_on_429(self):
        fail_resp = MagicMock()
        fail_resp.status_code = 429
        ok_resp = self._make_response([{"id": "r1", "title": "Retry success", "provider": "TV", "published": 1741564800}])

        session = MagicMock()
        session.get.side_effect = [fail_resp, ok_resp]

        client = TradingViewNewsClient(session=session, sleep_fn=lambda _: None)
        headlines = client.fetch_headlines("SPY")

        assert len(headlines) == 1
        assert session.get.call_count == 2

    def test_returns_empty_on_persistent_failure(self):
        fail_resp = MagicMock()
        fail_resp.status_code = 500

        session = MagicMock()
        session.get.return_value = fail_resp

        client = TradingViewNewsClient(
            config=TradingViewNewsConfig(max_retries=1),
            session=session,
            sleep_fn=lambda _: None,
        )
        headlines = client.fetch_headlines("SPY")

        assert headlines == []

    def test_fetch_batch(self):
        items = [{"id": "b1", "title": "Batch news", "provider": "TV", "published": 1741564800}]
        session = MagicMock()
        session.get.return_value = self._make_response(items)

        client = TradingViewNewsClient(session=session, sleep_fn=lambda _: None)
        result = client.fetch_batch(["SPY", "QQQ"])

        assert "SPY" in result
        assert "QQQ" in result
        assert len(result["SPY"]) == 1

    def test_sends_correct_params(self):
        session = MagicMock()
        session.get.return_value = self._make_response([])

        client = TradingViewNewsClient(session=session, sleep_fn=lambda _: None)
        client.fetch_headlines("MSFT", limit=5)

        call_kwargs = session.get.call_args[1]
        assert call_kwargs["params"]["symbol"] == "NASDAQ:MSFT"
        assert call_kwargs["params"]["limit"] == "5"
