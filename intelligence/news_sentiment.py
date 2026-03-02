"""News-feed normalization helpers for L6 scoring (F-004)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.signal.contracts import LayerScore
from app.signal.layers.news_sentiment import (
    DEFAULT_CONFIG,
    NewsArticle,
    NewsSentimentConfig,
    score_news_sentiment,
)


_SENTIMENT_LABELS: Dict[str, float] = {
    "very_bullish": 0.9,
    "strong_buy": 0.9,
    "bullish": 0.7,
    "positive": 0.6,
    "buy": 0.6,
    "slightly_positive": 0.3,
    "neutral": 0.0,
    "mixed": 0.0,
    "flat": 0.0,
    "slightly_negative": -0.3,
    "negative": -0.6,
    "bearish": -0.7,
    "sell": -0.6,
    "strong_sell": -0.9,
    "very_bearish": -0.9,
}


def _first(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload:
            value = payload.get(key)
            if value is not None:
                return value
    return None


def _to_iso8601_utc(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        # Support both seconds and milliseconds epoch.
        raw = float(value)
        if raw > 1_000_000_000_000:
            raw = raw / 1000.0
        parsed = datetime.fromtimestamp(raw, tz=timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z")

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def normalize_sentiment_value(value: Any) -> float:
    """Normalize numeric/label sentiment to [-1, 1]."""
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        numeric = float(value)
        # Some providers use 0-100 sentiment indexes.
        if numeric > 1.0 or numeric < -1.0:
            if 0.0 <= numeric <= 100.0:
                numeric = (numeric - 50.0) / 50.0
            else:
                numeric = numeric / 100.0
        return max(-1.0, min(1.0, numeric))

    text = str(value).strip().lower().replace(" ", "_")
    if not text:
        return 0.0

    if text.endswith("%"):
        try:
            pct = float(text[:-1])
            return max(-1.0, min(1.0, pct / 100.0))
        except ValueError:
            return 0.0

    if text in _SENTIMENT_LABELS:
        return _SENTIMENT_LABELS[text]

    try:
        numeric = float(text)
    except ValueError:
        return 0.0
    return normalize_sentiment_value(numeric)


def normalize_source_name(value: Any, default_source: str = "news-feed") -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return raw or str(default_source).strip().lower().replace(" ", "_") or "news-feed"


def normalize_news_item(
    payload: Mapping[str, Any],
    default_source: str = "news-feed",
    ticker_hint: str = "",
) -> Optional[NewsArticle]:
    """Normalize heterogeneous provider payload into NewsArticle."""
    ticker = _first(payload, ("ticker", "symbol", "instrument", "asset", "security"))
    if not ticker:
        ticker = ticker_hint
    ticker_text = str(ticker or "").strip().upper()
    if not ticker_text:
        return None

    headline = _first(payload, ("headline", "title", "summary"))
    headline_text = str(headline or "").strip()
    if not headline_text:
        return None

    published = _first(
        payload,
        ("published_at", "publishedAt", "pub_date", "pubDate", "date", "timestamp", "time"),
    )
    published_iso = _to_iso8601_utc(published)
    if not published_iso:
        return None

    sentiment_raw = _first(payload, ("sentiment", "sentiment_score", "sentimentScore", "polarity"))
    relevance_raw = _first(payload, ("relevance", "weight", "importance"))
    source_raw = _first(payload, ("source", "provider", "feed"))
    source_ref = _first(payload, ("id", "article_id", "articleId", "uuid", "url", "link")) or ""

    relevance = None
    if relevance_raw is not None:
        try:
            relevance = float(relevance_raw)
        except (TypeError, ValueError):
            relevance = None

    metadata = dict(payload)
    return NewsArticle(
        ticker=ticker_text,
        headline=headline_text,
        published_at=published_iso,
        sentiment=normalize_sentiment_value(sentiment_raw),
        source=normalize_source_name(source_raw, default_source=default_source),
        source_ref=str(source_ref),
        relevance=relevance,
        metadata=metadata,
    )


def normalize_news_feed(
    items: Iterable[Mapping[str, Any]],
    default_source: str = "news-feed",
    ticker_hint: str = "",
) -> List[NewsArticle]:
    """Normalize a feed payload list into valid NewsArticle rows."""
    normalized: List[NewsArticle] = []
    for payload in items or []:
        item = normalize_news_item(
            payload=payload,
            default_source=default_source,
            ticker_hint=ticker_hint,
        )
        if item:
            normalized.append(item)
    return normalized


def group_news_by_ticker(articles: Sequence[NewsArticle]) -> Dict[str, List[NewsArticle]]:
    grouped: Dict[str, List[NewsArticle]] = {}
    for item in articles or []:
        grouped.setdefault(item.ticker, []).append(item)
    return grouped


def score_news_feed_for_ticker(
    ticker: str,
    items: Iterable[Mapping[str, Any]],
    as_of: str,
    default_source: str = "news-feed",
    config: NewsSentimentConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Normalize feed items and score one ticker."""
    normalized = normalize_news_feed(
        items=items,
        default_source=default_source,
        ticker_hint=ticker,
    )
    return score_news_sentiment(
        ticker=ticker,
        articles=normalized,
        as_of=as_of,
        config=config,
    )


def score_news_feed(
    items: Iterable[Mapping[str, Any]],
    as_of: str,
    default_source: str = "news-feed",
    config: NewsSentimentConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Normalize and score a mixed-ticker feed payload."""
    normalized = normalize_news_feed(items=items, default_source=default_source)
    grouped = group_news_by_ticker(normalized)
    scores: Dict[str, LayerScore] = {}
    for ticker, ticker_items in grouped.items():
        scores[ticker] = score_news_sentiment(
            ticker=ticker,
            articles=ticker_items,
            as_of=as_of,
            config=config,
        )
    return scores
