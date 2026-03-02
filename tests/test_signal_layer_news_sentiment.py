"""Tests for F-004: L6 news sentiment scorer and feed normalizer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.signal.layers.news_sentiment import (
    NewsArticle,
    NewsSentimentConfig,
    score_news_sentiment,
    score_news_sentiment_batch,
)
from app.signal.types import LayerId
from intelligence.news_sentiment import (
    normalize_news_feed,
    normalize_news_item,
    normalize_sentiment_value,
    score_news_feed,
    score_news_feed_for_ticker,
)


AS_OF = "2026-03-02T12:00:00Z"


def _iso_hours_ago(hours: int) -> str:
    now = datetime.fromisoformat(AS_OF.replace("Z", "+00:00"))
    dt = now - timedelta(hours=hours)
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _article(
    ticker: str,
    sentiment: float,
    hours_ago: int,
    source: str = "seeking_alpha",
    relevance: float = 1.0,
) -> NewsArticle:
    return NewsArticle(
        ticker=ticker,
        headline=f"{ticker} headline {hours_ago}",
        published_at=_iso_hours_ago(hours_ago),
        sentiment=sentiment,
        source=source,
        relevance=relevance,
        source_ref=f"{ticker}-{hours_ago}-{source}",
    )


class TestNormalizer:
    def test_normalize_sentiment_value_supports_labels_numbers_and_pct(self):
        assert normalize_sentiment_value("bullish") == pytest.approx(0.7)
        assert normalize_sentiment_value("very_bearish") == pytest.approx(-0.9)
        assert normalize_sentiment_value(0.4) == pytest.approx(0.4)
        assert normalize_sentiment_value(80) == pytest.approx(0.6)
        assert normalize_sentiment_value("75%") == pytest.approx(0.75)
        assert normalize_sentiment_value("nonsense") == pytest.approx(0.0)

    def test_normalize_news_item_handles_mixed_schema(self):
        raw = {
            "symbol": "msft",
            "title": "Microsoft beats expectations",
            "publishedAt": "2026-03-02T10:00:00Z",
            "sentimentScore": 72,
            "provider": "Reuters",
            "articleId": "abc-1",
            "weight": 1.2,
        }
        item = normalize_news_item(raw)
        assert item is not None
        assert item.ticker == "MSFT"
        assert item.source == "reuters"
        assert item.sentiment == pytest.approx(0.44)
        assert item.relevance == pytest.approx(1.2)

    def test_normalize_news_item_supports_epoch_timestamp(self):
        epoch = int(datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc).timestamp())
        raw = {
            "ticker": "AAPL",
            "headline": "AAPL launch event announced",
            "timestamp": epoch,
            "sentiment": "positive",
        }
        item = normalize_news_item(raw)
        assert item is not None
        assert item.published_at.startswith("2026-03-02T10:00:00")

    def test_normalize_news_item_date_only_uses_midday_utc(self):
        raw = {
            "ticker": "AAPL",
            "headline": "Date only item",
            "date": "2026-03-02",
            "sentiment": "neutral",
        }
        item = normalize_news_item(raw)
        assert item is not None
        assert item.published_at.startswith("2026-03-02T12:00:00")

    def test_normalize_news_feed_skips_invalid_rows(self):
        items = [
            {"ticker": "AAPL", "headline": "ok", "published_at": "2026-03-02T10:00:00Z"},
            {"ticker": "", "headline": "missing ticker", "published_at": "2026-03-02T10:00:00Z"},
            {"ticker": "MSFT", "title": "", "published_at": "2026-03-02T10:00:00Z"},
            {"ticker": "TSLA", "title": "bad time", "published_at": "bad"},
        ]
        normalized = normalize_news_feed(items)
        assert len(normalized) == 1
        assert normalized[0].ticker == "AAPL"


class TestNewsSentimentScorer:
    def test_no_articles_returns_zero_placeholder(self):
        score = score_news_sentiment("SPY", [], AS_OF)
        assert score.layer_id == LayerId.L6_NEWS_SENTIMENT
        assert score.score == pytest.approx(0.0)
        assert score.confidence == 0.0
        assert score.details["reason"] == "no_articles_in_window"
        assert score.details["article_count"] == 0

    def test_strong_positive_with_volume_scores_high(self):
        articles = [
            _article("AAPL", 0.85, 1, source="seeking_alpha"),
            _article("AAPL", 0.8, 2, source="reuters"),
            _article("AAPL", 0.75, 3, source="bloomberg"),
            _article("AAPL", 0.7, 4, source="marketwatch"),
            _article("AAPL", 0.9, 5, source="financial_times"),
            _article("AAPL", 0.7, 6, source="seeking_alpha"),
            _article("AAPL", 0.8, 7, source="reuters"),
            _article("AAPL", 0.75, 8, source="bloomberg"),
            _article("AAPL", 0.7, 9, source="marketwatch"),
            _article("AAPL", 0.9, 10, source="financial_times"),
        ]
        score = score_news_sentiment("AAPL", articles, AS_OF)
        assert score.score >= 80.0
        assert score.confidence > 0.7
        assert score.details["sentiment_polarity"] > 0.65
        assert score.details["article_count"] == 10
        assert score.details["negative_article_ratio"] < 0.1
        assert "news-AAPL-2026-03-02-10a-" in score.provenance_ref

    def test_neutral_mix_lands_mid_band(self):
        articles = [
            _article("MSFT", 0.2, 2, source="reuters"),
            _article("MSFT", -0.1, 4, source="bloomberg"),
            _article("MSFT", 0.0, 6, source="x"),
            _article("MSFT", 0.1, 8, source="marketwatch"),
        ]
        score = score_news_sentiment("MSFT", articles, AS_OF)
        assert 40.0 <= score.score <= 65.0
        assert abs(score.details["sentiment_polarity"]) < 0.25

    def test_negative_spike_penalizes_hard(self):
        articles = []
        for i in range(1, 13):
            articles.append(_article("TSLA", -0.9, i % 10, source="reuters"))
        score = score_news_sentiment("TSLA", articles, AS_OF)
        assert score.score <= 15.0
        assert score.details["negative_article_ratio"] >= 0.8
        assert score.details["sub_scores"]["negative_penalty"] <= -20.0

    def test_source_weight_biases_quality_sources(self):
        articles = [
            _article("NVDA", 0.9, 1, source="x"),
            _article("NVDA", -0.6, 1, source="seeking_alpha"),
        ]
        score = score_news_sentiment("NVDA", articles, AS_OF)
        assert score.details["sentiment_polarity"] < 0.0

    def test_old_articles_outside_window_are_ignored(self):
        cfg = NewsSentimentConfig(window_hours=24)
        articles = [
            _article("AMD", 0.9, 30, source="reuters"),
            _article("AMD", 0.8, 40, source="seeking_alpha"),
        ]
        score = score_news_sentiment("AMD", articles, AS_OF, config=cfg)
        assert score.score == pytest.approx(0.0)
        assert score.details["reason"] == "no_articles_in_window"

    def test_required_contract_keys_exist_in_details(self):
        articles = [
            _article("SPY", 0.5, 3, source="reuters"),
            _article("SPY", -0.2, 4, source="seeking_alpha"),
        ]
        score = score_news_sentiment("SPY", articles, AS_OF)
        for key in ("sentiment_polarity", "article_count", "negative_article_ratio", "window_hours"):
            assert key in score.details

    def test_provenance_is_deterministic(self):
        articles = [
            _article("QQQ", 0.6, 1, source="reuters"),
            _article("QQQ", 0.1, 3, source="marketwatch"),
        ]
        score_a = score_news_sentiment("QQQ", articles, AS_OF)
        score_b = score_news_sentiment("QQQ", articles, AS_OF)
        assert score_a.provenance_ref == score_b.provenance_ref

    def test_relevance_multiplier_changes_polarity_weighting(self):
        articles = [
            _article("NFLX", 0.9, 1, source="reuters", relevance=0.25),
            _article("NFLX", -0.9, 1, source="reuters", relevance=1.5),
        ]
        score = score_news_sentiment("NFLX", articles, AS_OF)
        assert score.details["sentiment_polarity"] < 0.0

    def test_extreme_negative_clamps_to_zero(self):
        articles = [
            _article("PLTR", -1.0, i % 8, source="reuters")
            for i in range(1, 16)
        ]
        score = score_news_sentiment("PLTR", articles, AS_OF)
        assert score.score == pytest.approx(0.0)

    def test_confidence_formula_reaches_one_with_depth_diversity_and_freshness(self):
        sources = ("reuters", "bloomberg", "financial_times", "marketwatch", "seeking_alpha")
        articles = []
        for idx in range(12):
            articles.append(
                _article(
                    "IBM",
                    0.2,
                    0 if idx == 0 else 1,
                    source=sources[idx % len(sources)],
                )
            )
        score = score_news_sentiment("IBM", articles, AS_OF)
        assert score.confidence == pytest.approx(1.0)

    def test_layer_batch_scoring_returns_per_ticker_scores(self):
        batch = {
            "AAPL": [_article("AAPL", 0.6, 2, source="reuters")],
            "MSFT": [_article("MSFT", -0.4, 2, source="reuters")],
        }
        scores = score_news_sentiment_batch(batch, AS_OF)
        assert set(scores.keys()) == {"AAPL", "MSFT"}
        assert scores["AAPL"].score > scores["MSFT"].score


class TestFeedScoringHelpers:
    def test_score_news_feed_for_ticker_normalizes_and_scores(self):
        raw = [
            {
                "symbol": "META",
                "title": "Meta announces partnership",
                "publishedAt": "2026-03-02T08:00:00Z",
                "sentiment": "bullish",
                "provider": "Reuters",
                "id": "a1",
            },
            {
                "ticker": "META",
                "headline": "Mixed reception on launch",
                "published_at": "2026-03-02T09:00:00Z",
                "sentiment_score": -0.1,
                "source": "x",
                "id": "a2",
            },
        ]
        score = score_news_feed_for_ticker("META", raw, AS_OF)
        assert score.layer_id == LayerId.L6_NEWS_SENTIMENT
        assert score.ticker == "META"
        assert score.details["article_count"] == 2

    def test_score_news_feed_groups_by_ticker(self):
        raw = [
            {
                "ticker": "AAPL",
                "headline": "AAPL positive",
                "published_at": "2026-03-02T09:00:00Z",
                "sentiment": 0.7,
                "source": "Reuters",
            },
            {
                "ticker": "MSFT",
                "headline": "MSFT negative",
                "published_at": "2026-03-02T09:30:00Z",
                "sentiment": -0.5,
                "source": "Reuters",
            },
        ]
        scores = score_news_feed(raw, AS_OF)
        assert set(scores.keys()) == {"AAPL", "MSFT"}
        assert scores["AAPL"].score > scores["MSFT"].score
