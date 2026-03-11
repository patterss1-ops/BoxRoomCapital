"""Tests for automated feed aggregator (Engine B fuel)."""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from enum import Enum
from unittest.mock import MagicMock, patch

import pytest


def _reload_config(**env_overrides):
    with patch.dict("os.environ", env_overrides, clear=False):
        import config
        return importlib.reload(config)


# ── Fake data classes matching existing client return types ────────────────


class _FakeDirection(Enum):
    UP = "up"
    DOWN = "down"
    MAINTAINED = "maintained"


class _FakeEstimateType(Enum):
    PRICE_TARGET = "price_target"


@dataclass
class _FakeArticle:
    ticker: str
    headline: str
    published_at: str = "2026-03-10T12:00:00"
    sentiment: float = 0.0
    source: str = "finnhub"
    relevance: float = 0.8


@dataclass
class _FakeHeadline:
    headline_id: str
    title: str
    provider: str
    published_at: str = "2026-03-10T12:00:00"
    ticker: str = "SPY"
    story_path: str = ""


@dataclass
class _FakeRevision:
    ticker: str
    analyst_name: str
    direction: _FakeDirection
    estimate_type: _FakeEstimateType
    change_pct: float
    revision_date: str
    source_ref: str = "alpha-vantage"


class TestConfigDefaults:
    def test_feed_aggregator_disabled_by_default(self):
        cfg = _reload_config(FEED_AGGREGATOR_ENABLED="")
        assert cfg.FEED_AGGREGATOR_ENABLED is False

    def test_default_tickers(self):
        cfg = _reload_config()
        assert "SPY" in cfg.FEED_AGGREGATOR_TICKERS
        assert "QQQ" in cfg.FEED_AGGREGATOR_TICKERS

    def test_default_intervals(self):
        cfg = _reload_config()
        assert cfg.FEED_AGGREGATOR_FINNHUB_INTERVAL == 300
        assert cfg.FEED_AGGREGATOR_AV_INTERVAL == 900
        assert cfg.FEED_AGGREGATOR_FRED_INTERVAL == 3600

    def test_default_fred_series(self):
        cfg = _reload_config()
        assert "T10Y2Y" in cfg.FEED_AGGREGATOR_FRED_SERIES

    def test_tv_interval_default(self):
        cfg = _reload_config()
        assert cfg.FEED_AGGREGATOR_TV_INTERVAL == 600

    def test_tv_enabled_by_default(self):
        cfg = _reload_config()
        assert cfg.FEED_AGGREGATOR_TV_ENABLED is True


class TestFinnhubPoll:
    def test_submits_articles_with_correct_source_class(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        articles = [
            _FakeArticle(ticker="SPY", headline="Markets rally on jobs data"),
            _FakeArticle(ticker="SPY", headline="Fed signals caution"),
        ]
        finnhub = MagicMock()
        finnhub.fetch_company_news.return_value = articles
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=finnhub,
            av_client=MagicMock(),
            fred_client=MagicMock(),
            submit_fn=submit,
            tickers=["SPY"],
            fred_series=[],
        )
        count = svc.poll_finnhub_news()

        assert count == 2
        assert submit.call_count == 2
        for call in submit.call_args_list:
            assert call[1]["source_class"] == "news_wire"
            assert call[1]["source_credibility"] == 0.80

    def test_dedup_same_articles_only_first_batch(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        articles = [_FakeArticle(ticker="SPY", headline="Same headline")]
        finnhub = MagicMock()
        finnhub.fetch_company_news.return_value = articles
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=finnhub,
            av_client=MagicMock(),
            fred_client=MagicMock(),
            submit_fn=submit,
            tickers=["SPY"],
            fred_series=[],
        )
        first = svc.poll_finnhub_news()
        second = svc.poll_finnhub_news()

        assert first == 1
        assert second == 0
        assert submit.call_count == 1


class TestAlphaVantagePoll:
    def test_submits_revisions(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        revisions = [
            _FakeRevision(
                ticker="AAPL",
                analyst_name="Goldman Sachs",
                direction=_FakeDirection.UP,
                estimate_type=_FakeEstimateType.PRICE_TARGET,
                change_pct=12.5,
                revision_date="2026-03-10",
            )
        ]
        av = MagicMock()
        av.fetch_analyst_revisions.return_value = revisions
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=av,
            fred_client=MagicMock(),
            submit_fn=submit,
            tickers=["AAPL"],
            fred_series=[],
        )
        count = svc.poll_av_analyst_ratings()

        assert count == 1
        assert submit.call_args[1]["source_class"] == "analyst_revision"
        assert submit.call_args[1]["source_credibility"] == 0.85


class TestFREDPoll:
    def test_submits_observations(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        fred = MagicMock()
        fred.fetch_latest_value.return_value = -0.42
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=fred,
            submit_fn=submit,
            tickers=[],
            fred_series=["T10Y2Y"],
        )
        count = svc.poll_fred_macro()

        assert count == 1
        assert "T10Y2Y" in submit.call_args[1]["raw_content"]

    def test_skips_when_no_data(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        fred = MagicMock()
        fred.fetch_latest_value.return_value = None
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=fred,
            submit_fn=submit,
            tickers=[],
            fred_series=["T10Y2Y"],
        )
        count = svc.poll_fred_macro()

        assert count == 0
        assert submit.call_count == 0


class TestStatus:
    def test_reports_per_source_counts(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        articles = [_FakeArticle(ticker="SPY", headline="Test")]
        finnhub = MagicMock()
        finnhub.fetch_company_news.return_value = articles
        fred = MagicMock()
        fred.fetch_latest_value.return_value = 1.5

        svc = FeedAggregatorService(
            finnhub_client=finnhub,
            av_client=MagicMock(fetch_analyst_revisions=MagicMock(return_value=[])),
            fred_client=fred,
            submit_fn=MagicMock(),
            tickers=["SPY"],
            fred_series=["DFF"],
        )
        svc.poll_finnhub_news()
        svc.poll_fred_macro()

        status = svc.status()
        assert status["finnhub"]["submitted"] == 1
        assert status["fred"]["submitted"] == 1


class TestContentHash:
    def test_deterministic(self):
        from intelligence.feed_aggregator import content_hash

        h1 = content_hash("test string")
        h2 = content_hash("test string")
        assert h1 == h2
        assert len(h1) == 16


class TestDedupBound:
    def test_bounded_to_max_size(self):
        from intelligence.feed_aggregator import _BoundedHashSet

        s = _BoundedHashSet(max_size=5)
        for i in range(20):
            s.add(f"key_{i}")

        assert len(s) == 5
        # Most recent entries should be kept
        assert "key_19" in s
        assert "key_0" not in s


class TestBotControlServiceLifecycle:
    def test_start_stop(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        with patch("app.engine.control.config") as mock_config:
            mock_config.FEED_AGGREGATOR_ENABLED = True
            mock_config.FEED_AGGREGATOR_TICKERS = ["SPY"]
            mock_config.FEED_AGGREGATOR_FRED_SERIES = ["T10Y2Y"]
            mock_config.FEED_AGGREGATOR_FINNHUB_INTERVAL = 300
            mock_config.FEED_AGGREGATOR_AV_INTERVAL = 900
            mock_config.FEED_AGGREGATOR_FRED_INTERVAL = 3600
            mock_config.FEED_AGGREGATOR_TV_INTERVAL = 600
            mock_config.FEED_AGGREGATOR_TV_ENABLED = False
            mock_config.LOG_FILE = "test.log"

            result = svc.start_feed_aggregator()
            assert result["status"] == "started"

            status = svc.feed_aggregator_status()
            assert status["running"] is True

            stop_result = svc.stop_feed_aggregator()
            assert stop_result["status"] == "stopped"

    def test_disabled_returns_disabled(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)
        result = svc.start_feed_aggregator()
        assert result["status"] == "disabled"


class TestAutoRestart:
    def test_watchdog_restarts_on_crash(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)
        svc._feed_aggregator = None  # simulate crash

        with patch("app.engine.control.config") as mock_config:
            mock_config.FEED_AGGREGATOR_ENABLED = True
            mock_config.FEED_AGGREGATOR_TICKERS = ["SPY"]
            mock_config.FEED_AGGREGATOR_FRED_SERIES = ["T10Y2Y"]
            mock_config.FEED_AGGREGATOR_FINNHUB_INTERVAL = 300
            mock_config.FEED_AGGREGATOR_AV_INTERVAL = 900
            mock_config.FEED_AGGREGATOR_FRED_INTERVAL = 3600
            mock_config.FEED_AGGREGATOR_TV_INTERVAL = 600
            mock_config.FEED_AGGREGATOR_TV_ENABLED = False
            mock_config.ORCHESTRATOR_ENABLED = False
            mock_config.DISPATCHER_ENABLED = False
            mock_config.ENGINE_A_ENABLED = False
            mock_config.ENGINE_B_ENABLED = False
            mock_config.INTRADAY_ENABLED = False
            mock_config.LOG_FILE = "test.log"

            result = svc.check_and_restart()

        assert "feed_aggregator" in result


class TestTradingViewPoll:
    def test_submits_headlines_with_correct_source_class(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        headlines = [
            _FakeHeadline(headline_id="tv1", title="Apple beats earnings", provider="Reuters", ticker="AAPL"),
            _FakeHeadline(headline_id="tv2", title="Tech stocks surge", provider="Bloomberg", ticker="AAPL"),
        ]
        tv = MagicMock()
        tv.fetch_headlines.return_value = headlines
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=MagicMock(),
            submit_fn=submit,
            tickers=["AAPL"],
            fred_series=[],
            tv_client=tv,
        )
        count = svc.poll_tradingview_news()

        assert count == 2
        assert submit.call_count == 2
        for call in submit.call_args_list:
            assert call[1]["source_class"] == "news_wire"
            assert call[1]["source_credibility"] == 0.75

    def test_dedup_same_headlines(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        headlines = [_FakeHeadline(headline_id="dup1", title="Same news", provider="TV")]
        tv = MagicMock()
        tv.fetch_headlines.return_value = headlines
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=MagicMock(),
            submit_fn=submit,
            tickers=["SPY"],
            fred_series=[],
            tv_client=tv,
        )
        first = svc.poll_tradingview_news()
        second = svc.poll_tradingview_news()

        assert first == 1
        assert second == 0

    def test_skipped_when_no_tv_client(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=MagicMock(),
            submit_fn=MagicMock(),
            tickers=["SPY"],
            fred_series=[],
            tv_client=None,
        )
        count = svc.poll_tradingview_news()
        assert count == 0

    def test_status_includes_tradingview(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        tv = MagicMock()
        tv.fetch_headlines.return_value = [
            _FakeHeadline(headline_id="s1", title="Status test", provider="TV")
        ]

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=MagicMock(),
            submit_fn=MagicMock(),
            tickers=["SPY"],
            fred_series=[],
            tv_client=tv,
        )
        svc.poll_tradingview_news()
        status = svc.status()

        assert status["tradingview"]["submitted"] == 1
        assert status["tradingview"]["enabled"] is True

    def test_tv_error_doesnt_block_other_sources(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        tv = MagicMock()
        tv.fetch_headlines.side_effect = RuntimeError("TV down")
        fred = MagicMock()
        fred.fetch_latest_value.return_value = 3.0
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=MagicMock(),
            av_client=MagicMock(),
            fred_client=fred,
            submit_fn=submit,
            tickers=["SPY"],
            fred_series=["DFF"],
            tv_client=tv,
        )
        svc.poll_tradingview_news()
        fred_count = svc.poll_fred_macro()

        assert fred_count == 1
        status = svc.status()
        assert status["tradingview"]["errors"] == 1
        assert status["fred"]["submitted"] == 1


class TestClientErrorIsolation:
    def test_finnhub_error_doesnt_block_fred(self):
        from intelligence.feed_aggregator import FeedAggregatorService

        finnhub = MagicMock()
        finnhub.fetch_company_news.side_effect = RuntimeError("API down")
        fred = MagicMock()
        fred.fetch_latest_value.return_value = 2.5
        submit = MagicMock()

        svc = FeedAggregatorService(
            finnhub_client=finnhub,
            av_client=MagicMock(),
            fred_client=fred,
            submit_fn=submit,
            tickers=["SPY"],
            fred_series=["DFF"],
        )

        svc.poll_finnhub_news()  # Should not raise
        fred_count = svc.poll_fred_macro()

        assert fred_count == 1
        status = svc.status()
        assert status["finnhub"]["errors"] == 1
        assert status["fred"]["submitted"] == 1
