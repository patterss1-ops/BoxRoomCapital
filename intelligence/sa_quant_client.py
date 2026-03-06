"""L8 Seeking Alpha Quant adapter (E-003).

Fetches SA Quant data via RapidAPI and normalizes it into LayerScore payloads
for the Signal Engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
import time
from typing import Any, Dict, Mapping, Optional, Sequence

import requests

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


DEFAULT_SOURCE = "sa-quant-rapidapi"
DEFAULT_HOST = "seeking-alpha.p.rapidapi.com"
DEFAULT_ENDPOINT = f"https://{DEFAULT_HOST}/symbols/get-ratings"
FACTOR_GRADES_ENDPOINT = f"https://{DEFAULT_HOST}/symbols/get-metrics-grades"
NEWS_ENDPOINT = f"https://{DEFAULT_HOST}/news/get-news"
ANALYST_RECS_ENDPOINT = f"https://{DEFAULT_HOST}/symbols/get-analyst-recommendation"
MARKET_OUTLOOK_ENDPOINT = f"https://{DEFAULT_HOST}/market-outlook/get-outlook"

_RATING_KEYS: Sequence[str] = (
    "quant_rating",
    "quantRating",
    "quant_rating_label",
    "quantRecommendation",
    "quant_recommendation",
    "rating",
)

_SCORE_KEYS: Sequence[str] = (
    "quant_score",
    "quantScore",
    "quant_rating_score",
    "ratingScore",
    "score",
)

_SECTOR_RANK_KEYS: Sequence[str] = (
    "sector_rank",
    "sectorRank",
    "sector_rank_percentile",
)

_INDUSTRY_RANK_KEYS: Sequence[str] = (
    "industry_rank",
    "industryRank",
    "industry_rank_percentile",
)

_UPDATED_KEYS: Sequence[str] = (
    "updated_at",
    "updatedAt",
    "last_updated",
    "lastUpdated",
    "as_of",
    "asOf",
)


_RATING_TO_SCORE: Dict[str, float] = {
    "very bullish": 95.0,
    "bullish": 80.0,
    "neutral": 50.0,
    "bearish": 20.0,
    "very bearish": 5.0,
    "strong buy": 90.0,
    "buy": 75.0,
    "hold": 50.0,
    "sell": 25.0,
    "strong sell": 10.0,
}


class SAQuantClientError(RuntimeError):
    """Raised when SA Quant API calls fail."""

    def __init__(self, message: str, status_code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class SAQuantParseError(ValueError):
    """Raised when a payload cannot be parsed into quant fields."""


@dataclass(frozen=True)
class SAQuantSnapshot:
    """Normalized SA Quant snapshot for one ticker."""

    ticker: str
    rating: str = ""
    quant_score_raw: Optional[float] = None
    sector_rank: Optional[float] = None
    industry_rank: Optional[float] = None
    updated_at: str = ""
    source_ref: str = ""
    raw_fields: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SAQuantClientConfig:
    """HTTP client configuration for RapidAPI SA Quant access."""

    api_key: str = ""
    host: str = DEFAULT_HOST
    endpoint: str = DEFAULT_ENDPOINT
    timeout_seconds: float = 10.0
    max_retries: int = 2
    backoff_seconds: float = 0.25
    source: str = DEFAULT_SOURCE


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_value(node: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in node and node[key] is not None:
            return node[key]
    return None


def _normalize_rating(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _score_from_rating_text(rating: str) -> Optional[float]:
    if not rating:
        return None
    return _RATING_TO_SCORE.get(_normalize_rating(rating))


def _score_from_numeric(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None

    numeric = float(value)
    if numeric < 0:
        return 0.0

    # Percentile-style value
    if numeric <= 1.0:
        return numeric * 100.0
    # 0..5 scale (common in quant rating feeds)
    if numeric <= 5.0:
        return numeric * 20.0
    # 0..10 scale fallback
    if numeric <= 10.0:
        return numeric * 10.0
    # Already 0..100 scale
    if numeric <= 100.0:
        return numeric
    return 100.0


def _iter_payload_nodes(payload: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    nodes: list[Mapping[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, Mapping):
                nodes.append(item)
    elif isinstance(data, Mapping):
        nodes.append(data)
    nodes.append(payload)
    return nodes


def parse_sa_quant_payload(ticker: str, payload: Mapping[str, Any]) -> SAQuantSnapshot:
    """Parse varying RapidAPI payload shapes into a SAQuantSnapshot."""
    symbol = ticker.strip().upper()

    for node in _iter_payload_nodes(payload):
        attrs_obj = node.get("attributes") if isinstance(node, Mapping) else None
        attrs = attrs_obj if isinstance(attrs_obj, Mapping) else node

        rating = _extract_value(attrs, _RATING_KEYS)
        quant_score = _coerce_float(_extract_value(attrs, _SCORE_KEYS))

        if rating is None and quant_score is None:
            continue

        sector_rank = _coerce_float(_extract_value(attrs, _SECTOR_RANK_KEYS))
        industry_rank = _coerce_float(_extract_value(attrs, _INDUSTRY_RANK_KEYS))
        updated_value = _extract_value(attrs, _UPDATED_KEYS)
        updated_at = str(updated_value).strip() if updated_value is not None else ""

        source_ref = ""
        source_value = node.get("id") if isinstance(node, Mapping) else None
        if source_value is None:
            meta = payload.get("meta") if isinstance(payload, Mapping) else None
            if isinstance(meta, Mapping):
                source_value = meta.get("request_id") or meta.get("requestId")
        if source_value is not None:
            source_ref = str(source_value).strip()

        snapshot_fields = {
            "rating": str(rating).strip() if rating is not None else "",
            "quant_score_raw": quant_score,
            "sector_rank": sector_rank,
            "industry_rank": industry_rank,
            "updated_at": updated_at,
        }

        return SAQuantSnapshot(
            ticker=symbol,
            rating=snapshot_fields["rating"],
            quant_score_raw=snapshot_fields["quant_score_raw"],
            sector_rank=snapshot_fields["sector_rank"],
            industry_rank=snapshot_fields["industry_rank"],
            updated_at=snapshot_fields["updated_at"],
            source_ref=source_ref,
            raw_fields=snapshot_fields,
        )

    raise SAQuantParseError(f"No quant rating fields found in SA payload for {symbol}.")


def score_sa_quant_snapshot(
    snapshot: SAQuantSnapshot,
    as_of: str,
    source: str = DEFAULT_SOURCE,
) -> LayerScore:
    """Convert a normalized SAQuantSnapshot into LayerScore (L8)."""
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    rating_score = _score_from_rating_text(snapshot.rating)
    numeric_score = _score_from_numeric(snapshot.quant_score_raw)

    if rating_score is not None and numeric_score is not None:
        score = 0.7 * rating_score + 0.3 * numeric_score
        confidence = 0.95
    elif rating_score is not None:
        score = rating_score
        confidence = 0.8
    elif numeric_score is not None:
        score = numeric_score
        confidence = 0.7
    else:
        score = 0.0
        confidence = 0.0

    score = max(0.0, min(100.0, score))

    details: Dict[str, Any] = {
        "rating": snapshot.rating,
        "quant_score_raw": snapshot.quant_score_raw,
        "sector_rank": snapshot.sector_rank,
        "industry_rank": snapshot.industry_rank,
        "updated_at": snapshot.updated_at,
        "rating_score": rating_score,
        "numeric_score": numeric_score,
    }
    raw_fields = snapshot.raw_fields or {}
    if isinstance(raw_fields, Mapping):
        if raw_fields.get("primary_price") is not None:
            details["primary_price"] = raw_fields.get("primary_price")
        if raw_fields.get("overall_rank") is not None:
            details["overall_rank"] = raw_fields.get("overall_rank")
        if raw_fields.get("sector_name"):
            details["sector_name"] = raw_fields.get("sector_name")
        if raw_fields.get("industry_name"):
            details["industry_name"] = raw_fields.get("industry_name")
        section_names = raw_fields.get("normalized_section_names") or raw_fields.get("section_names")
        if isinstance(section_names, Sequence) and not isinstance(section_names, (str, bytes)):
            details["section_names"] = list(section_names)

    provenance_seed = (
        f"{snapshot.ticker}|{as_of_dt.date().isoformat()}|"
        f"{snapshot.rating}|{snapshot.quant_score_raw}|{snapshot.updated_at}"
    )
    provenance_hash = hashlib.sha256(provenance_seed.encode("utf-8")).hexdigest()[:12]
    provenance_ref = f"sa-quant-{snapshot.ticker}-{as_of_dt.date().isoformat()}-{provenance_hash}"

    return LayerScore(
        layer_id=LayerId.L8_SA_QUANT,
        ticker=snapshot.ticker,
        score=round(score, 2),
        as_of=as_of,
        source=source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_sa_quant_payload(
    ticker: str,
    payload: Mapping[str, Any],
    as_of: str,
    source: str = DEFAULT_SOURCE,
) -> LayerScore:
    """Convenience helper for one-shot payload -> LayerScore conversion."""
    snapshot = parse_sa_quant_payload(ticker=ticker, payload=payload)
    return score_sa_quant_snapshot(snapshot=snapshot, as_of=as_of, source=source)


class SAQuantClient:
    """RapidAPI client for SA Quant ticker ratings."""

    def __init__(
        self,
        config: Optional[SAQuantClientConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        cfg = config or SAQuantClientConfig()
        api_key = cfg.api_key.strip() or os.getenv("SA_RAPIDAPI_KEY", "").strip()
        self.config = SAQuantClientConfig(
            api_key=api_key,
            host=cfg.host,
            endpoint=cfg.endpoint,
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff_seconds=cfg.backoff_seconds,
            source=cfg.source,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _headers(self) -> Dict[str, str]:
        if not self.config.api_key:
            raise SAQuantClientError("SA_RAPIDAPI_KEY is not configured.", retryable=False)
        return {
            "X-RapidAPI-Key": self.config.api_key,
            "X-RapidAPI-Host": self.config.host,
        }

    def _request_json(self, ticker: str) -> Dict[str, Any]:
        symbol = ticker.strip().upper()
        if not symbol:
            raise SAQuantClientError("ticker is required.", retryable=False)

        last_error: Optional[Exception] = None
        retries = max(0, int(self.config.max_retries))

        for attempt in range(retries + 1):
            try:
                response = self._session.get(
                    self.config.endpoint,
                    headers=self._headers(),
                    params={"symbols": symbol},
                    timeout=float(self.config.timeout_seconds),
                )
            except requests.RequestException as exc:
                last_error = exc
                retryable = True
                if attempt >= retries:
                    raise SAQuantClientError(
                        f"SA Quant request failed for {symbol}: {exc}",
                        retryable=retryable,
                    ) from exc
                self._sleep(self.config.backoff_seconds * (2 ** attempt))
                continue

            status = int(response.status_code)
            if status == 429 or 500 <= status <= 599:
                last_error = SAQuantClientError(
                    f"SA Quant transient HTTP {status} for {symbol}.",
                    status_code=status,
                    retryable=True,
                )
                if attempt >= retries:
                    raise last_error
                self._sleep(self.config.backoff_seconds * (2 ** attempt))
                continue

            if status >= 400:
                raise SAQuantClientError(
                    f"SA Quant HTTP {status} for {symbol}.",
                    status_code=status,
                    retryable=False,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise SAQuantClientError(
                    f"SA Quant returned invalid JSON for {symbol}.",
                    retryable=False,
                ) from exc

            if not isinstance(payload, Mapping):
                raise SAQuantClientError(
                    f"SA Quant payload for {symbol} is not a JSON object.",
                    retryable=False,
                )
            return dict(payload)

        if last_error:
            raise SAQuantClientError(str(last_error), retryable=True)
        raise SAQuantClientError(f"SA Quant request failed for {symbol}.", retryable=True)

    def fetch_payload(self, ticker: str) -> Dict[str, Any]:
        """Fetch raw SA Quant payload for a ticker."""
        return self._request_json(ticker)

    def fetch_snapshot(self, ticker: str) -> SAQuantSnapshot:
        """Fetch and parse SA Quant payload for a ticker."""
        payload = self.fetch_payload(ticker)
        return parse_sa_quant_payload(ticker=ticker, payload=payload)

    def fetch_layer_score(self, ticker: str, as_of: str) -> LayerScore:
        """Fetch SA Quant and return L8 LayerScore payload."""
        snapshot = self.fetch_snapshot(ticker)
        return score_sa_quant_snapshot(snapshot=snapshot, as_of=as_of, source=self.config.source)

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        """Fetch SA factor grades (value/growth/momentum/profitability/revisions).

        Returns raw grade data dict with keys like 'value_grade', 'growth_grade', etc.
        Uses the /symbols/get-metrics-grades endpoint.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return {}

        try:
            response = self._session.get(
                FACTOR_GRADES_ENDPOINT,
                headers=self._headers(),
                params={"symbols": symbol},
                timeout=float(self.config.timeout_seconds),
            )
            if response.status_code != 200:
                return {}

            payload = response.json()
            if not isinstance(payload, Mapping):
                return {}

            # Navigate to the grade data
            grades: Dict[str, Any] = {}
            for node in _iter_payload_nodes(payload):
                attrs = node.get("attributes", node) if isinstance(node, Mapping) else node
                if not isinstance(attrs, Mapping):
                    continue
                for key in ("value_grade", "growth_grade", "momentum_grade",
                            "profitability_grade", "revisions_grade",
                            "valueGrade", "growthGrade", "momentumGrade",
                            "profitabilityGrade", "revisionsGrade"):
                    val = attrs.get(key)
                    if val is not None:
                        normalized_key = key.replace("Grade", "_grade")
                        if not normalized_key.endswith("_grade"):
                            normalized_key = key
                        grades[normalized_key] = val
                if grades:
                    break
            return grades
        except Exception:
            return {}

    def fetch_news(self, ticker: str, count: int = 20) -> list[Dict[str, Any]]:
        """Fetch SA news articles for a ticker.

        Returns list of dicts with headline, published_at, source, url.
        Uses the /news/get-news endpoint.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        try:
            response = self._session.get(
                NEWS_ENDPOINT,
                headers=self._headers(),
                params={"symbol": symbol, "count": str(count)},
                timeout=float(self.config.timeout_seconds),
            )
            if response.status_code != 200:
                return []

            payload = response.json()
            articles = []
            items = payload.get("data", []) if isinstance(payload, Mapping) else payload
            if not isinstance(items, list):
                return []

            for item in items:
                if not isinstance(item, Mapping):
                    continue
                attrs = item.get("attributes", item) if isinstance(item, Mapping) else item
                if not isinstance(attrs, Mapping):
                    continue
                headline = attrs.get("title") or attrs.get("headline") or ""
                if headline:
                    articles.append({
                        "headline": str(headline),
                        "published_at": str(attrs.get("publishOn") or attrs.get("published_at") or ""),
                        "source": "seeking_alpha",
                        "url": str(attrs.get("url") or attrs.get("link") or ""),
                    })
            return articles
        except Exception:
            return []

    def fetch_analyst_recs(self, ticker: str) -> list[Dict[str, Any]]:
        """Fetch SA analyst recommendations (Wall Street consensus).

        Returns list of dicts with analyst, rating, target_price, date.
        Uses the /symbols/get-analyst-recommendation endpoint.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        try:
            response = self._session.get(
                ANALYST_RECS_ENDPOINT,
                headers=self._headers(),
                params={"symbols": symbol},
                timeout=float(self.config.timeout_seconds),
            )
            if response.status_code != 200:
                return []

            payload = response.json()
            recs = []
            items = payload.get("data", []) if isinstance(payload, Mapping) else payload
            if not isinstance(items, list):
                items = [payload]

            for item in items:
                if not isinstance(item, Mapping):
                    continue
                attrs = item.get("attributes", item)
                if not isinstance(attrs, Mapping):
                    continue
                recs.append({
                    "analyst": str(attrs.get("analyst") or attrs.get("firm") or ""),
                    "rating": str(attrs.get("rating") or attrs.get("recommendation") or ""),
                    "target_price": _coerce_float(attrs.get("target_price") or attrs.get("priceTarget")),
                    "date": str(attrs.get("date") or attrs.get("publishedAt") or ""),
                })
            return recs
        except Exception:
            return []
