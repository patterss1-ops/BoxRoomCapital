"""L6 News Sentiment scorer (F-004).

Computes a 0-100 sentiment score from normalized news articles, with:
- source-quality weighting
- recency weighting within a rolling window
- negativity-bias penalty for clustered negative coverage

This layer is intentionally deterministic and data-source agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


@dataclass(frozen=True)
class NewsArticle:
    """Normalized news item used by the L6 scorer."""

    ticker: str
    headline: str
    published_at: str
    sentiment: float
    source: str
    source_ref: str = ""
    relevance: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        object.__setattr__(self, "ticker", ticker)

        headline = str(self.headline or "").strip()
        if not headline:
            raise ValueError("headline is required.")
        object.__setattr__(self, "headline", headline)

        source = str(self.source or "").strip().lower().replace(" ", "_")
        if not source:
            raise ValueError("source is required.")
        object.__setattr__(self, "source", source)

        sentiment = float(self.sentiment)
        sentiment = max(-1.0, min(1.0, sentiment))
        object.__setattr__(self, "sentiment", sentiment)

        _parse_iso8601(self.published_at)
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

        if self.relevance is not None:
            relevance = float(self.relevance)
            relevance = max(0.25, min(1.5, relevance))
            object.__setattr__(self, "relevance", relevance)

    @property
    def published_datetime(self) -> datetime:
        return _parse_iso8601(self.published_at)


@dataclass(frozen=True)
class NewsSentimentConfig:
    """Tunable parameters for L6 scoring."""

    source: str = "news-sentiment"
    window_hours: int = 48
    negative_threshold: float = -0.25

    # (source_key, weight)
    source_weights: Tuple[Tuple[str, float], ...] = (
        ("seeking_alpha", 1.0),
        ("reuters", 0.95),
        ("bloomberg", 0.95),
        ("wall_street_journal", 0.9),
        ("financial_times", 0.9),
        ("marketwatch", 0.85),
        ("x", 0.55),
        ("twitter", 0.55),
        ("press_release", 0.45),
    )
    default_source_weight: float = 0.75

    # (min_article_count, score)
    volume_breakpoints: Tuple[Tuple[int, float], ...] = (
        (15, 20.0),
        (10, 16.0),
        (6, 12.0),
        (3, 8.0),
        (1, 4.0),
    )

    # (min_source_count, score)
    diversity_breakpoints: Tuple[Tuple[int, float], ...] = (
        (5, 10.0),
        (3, 7.0),
        (2, 5.0),
        (1, 2.0),
    )

    # (min_negative_ratio, penalty)
    negative_penalties: Tuple[Tuple[float, float], ...] = (
        (0.80, -30.0),
        (0.65, -20.0),
        (0.50, -12.0),
        (0.35, -6.0),
    )


DEFAULT_CONFIG = NewsSentimentConfig()


def _parse_iso8601(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required.")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid ISO timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_as_of(as_of: str) -> datetime:
    return _parse_iso8601(as_of)


def _source_weight(source: str, config: NewsSentimentConfig) -> float:
    normalized = str(source or "").strip().lower().replace(" ", "_")
    for key, weight in config.source_weights:
        if normalized == key:
            return float(weight)
    return float(config.default_source_weight)


def _filter_in_window(
    ticker: str,
    articles: Sequence[NewsArticle],
    as_of: datetime,
    window_hours: int,
) -> List[NewsArticle]:
    ticker_norm = str(ticker or "").strip().upper()
    cutoff = as_of - timedelta(hours=max(1, int(window_hours)))
    selected = []
    for item in articles:
        if item.ticker != ticker_norm:
            continue
        published = item.published_datetime
        if published > as_of:
            continue
        if published < cutoff:
            continue
        selected.append(item)
    return selected


def _recency_weight(article: NewsArticle, as_of: datetime, window_hours: int) -> float:
    age = max(0.0, (as_of - article.published_datetime).total_seconds() / 3600.0)
    if age >= float(window_hours):
        return 0.0
    linear = 1.0 - (age / float(window_hours))
    # Keep some floor within the active window so older items still contribute.
    return max(0.2, linear)


def _article_weight(article: NewsArticle, as_of: datetime, config: NewsSentimentConfig) -> float:
    source_component = _source_weight(article.source, config)
    recency_component = _recency_weight(article, as_of, config.window_hours)
    relevance_component = float(article.relevance if article.relevance is not None else 1.0)
    return max(0.0, source_component * recency_component * relevance_component)


def _score_volume(article_count: int, config: NewsSentimentConfig) -> float:
    for min_count, score in config.volume_breakpoints:
        if article_count >= min_count:
            return score
    return 0.0


def _score_diversity(source_count: int, config: NewsSentimentConfig) -> float:
    for min_count, score in config.diversity_breakpoints:
        if source_count >= min_count:
            return score
    return 0.0


def _negative_penalty(negative_ratio: float, config: NewsSentimentConfig) -> float:
    for min_ratio, penalty in config.negative_penalties:
        if negative_ratio >= min_ratio:
            return penalty
    return 0.0


def _polarity_to_score(polarity: float) -> float:
    # Polarity in [-1, 1] -> score in [0, 70].
    normalized = (float(polarity) + 1.0) / 2.0
    return max(0.0, min(70.0, normalized * 70.0))


def _compute_confidence(
    article_count: int,
    source_count: int,
    most_recent_age_hours: float,
    window_hours: int,
) -> float:
    density = min(float(article_count) / 12.0, 1.0)
    diversity = min(float(source_count) / 5.0, 1.0)
    freshness = max(0.0, 1.0 - (max(0.0, most_recent_age_hours) / float(window_hours)))
    return round(0.5 * density + 0.3 * diversity + 0.2 * freshness, 4)


def score_news_sentiment(
    ticker: str,
    articles: Sequence[NewsArticle],
    as_of: str,
    config: NewsSentimentConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Score normalized news sentiment for one ticker."""
    as_of_dt = _parse_as_of(as_of)
    as_of_text = as_of_dt.isoformat().replace("+00:00", "Z")
    ticker_norm = str(ticker or "").strip().upper()
    if not ticker_norm:
        raise ValueError("ticker is required.")

    filtered = _filter_in_window(
        ticker=ticker_norm,
        articles=articles,
        as_of=as_of_dt,
        window_hours=config.window_hours,
    )

    if not filtered:
        return LayerScore(
            layer_id=LayerId.L6_NEWS_SENTIMENT,
            ticker=ticker_norm,
            score=45.0,
            as_of=as_of_text,
            source=config.source,
            provenance_ref=f"news-{ticker_norm}-{as_of_text[:10]}-no-data",
            confidence=0.0,
            details={
                "sentiment_polarity": 0.0,
                "article_count": 0,
                "negative_article_ratio": 0.0,
                "window_hours": config.window_hours,
                "reason": "no_articles_in_window",
            },
        )

    weighted_sentiment = 0.0
    total_weight = 0.0
    negative_weight = 0.0
    source_set = set()
    weighted_age_hours = 0.0
    most_recent_age_hours = float(config.window_hours)

    for item in filtered:
        weight = _article_weight(item, as_of_dt, config)
        if weight <= 0.0:
            continue
        total_weight += weight
        weighted_sentiment += item.sentiment * weight
        source_set.add(item.source)

        age_hours = max(0.0, (as_of_dt - item.published_datetime).total_seconds() / 3600.0)
        weighted_age_hours += age_hours * weight
        most_recent_age_hours = min(most_recent_age_hours, age_hours)

        if item.sentiment <= float(config.negative_threshold):
            negative_weight += weight

    if total_weight <= 0.0:
        polarity = 0.0
        negative_ratio = 0.0
        avg_age_hours = float(config.window_hours)
    else:
        polarity = weighted_sentiment / total_weight
        polarity = max(-1.0, min(1.0, polarity))
        negative_ratio = negative_weight / total_weight
        avg_age_hours = weighted_age_hours / total_weight

    article_count = len(filtered)
    source_count = len(source_set)
    polarity_score = _polarity_to_score(polarity)
    volume_score = _score_volume(article_count, config)
    diversity_score = _score_diversity(source_count, config)
    penalty = _negative_penalty(negative_ratio, config)
    raw_score = polarity_score + volume_score + diversity_score + penalty
    final_score = round(max(0.0, min(100.0, raw_score)), 2)

    confidence = _compute_confidence(
        article_count=article_count,
        source_count=source_count,
        most_recent_age_hours=most_recent_age_hours,
        window_hours=config.window_hours,
    )

    details: Dict[str, Any] = {
        "sentiment_polarity": round(polarity, 4),
        "article_count": article_count,
        "negative_article_ratio": round(negative_ratio, 4),
        "window_hours": config.window_hours,
        "avg_age_hours": round(avg_age_hours, 4),
        "source_count": source_count,
        "sub_scores": {
            "polarity": round(polarity_score, 4),
            "volume": round(volume_score, 4),
            "diversity": round(diversity_score, 4),
            "negative_penalty": round(penalty, 4),
        },
        "raw_score": round(raw_score, 4),
    }

    polarity_bucket = int(round((polarity + 1.0) * 50.0))
    neg_bucket = int(round(negative_ratio * 100.0))
    provenance_ref = (
        f"news-{ticker_norm}-{as_of_text[:10]}-"
        f"{article_count}a-p{polarity_bucket:02d}-n{neg_bucket:02d}"
    )

    return LayerScore(
        layer_id=LayerId.L6_NEWS_SENTIMENT,
        ticker=ticker_norm,
        score=final_score,
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_news_sentiment_batch(
    articles_by_ticker: Mapping[str, Sequence[NewsArticle]],
    as_of: str,
    config: NewsSentimentConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Score news sentiment for many tickers in one pass."""
    result: Dict[str, LayerScore] = {}
    for ticker, articles in articles_by_ticker.items():
        ticker_norm = str(ticker or "").strip().upper()
        if not ticker_norm:
            continue
        result[ticker_norm] = score_news_sentiment(
            ticker=ticker_norm,
            articles=list(articles or []),
            as_of=as_of,
            config=config,
        )
    return result
