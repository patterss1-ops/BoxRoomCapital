"""Seeking Alpha fallback adapters.

Server-side SA scraping is routinely blocked by PerimeterX. This module keeps
three paths behind the same SA-quant interface:

1. `SABrowserCaptureAdapter` for authenticated browser/bookmarklet captures
2. `YFinnhubAdapter` for broad automated fallback coverage
3. shared payload normalization helpers for browser-captured SA pages
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from data.trade_db import DB_PATH, get_conn
from intelligence.sa_quant_client import SAQuantSnapshot, score_sa_quant_snapshot

logger = logging.getLogger(__name__)

SA_BROWSER_CAPTURE_EVENT_TYPE = "sa_browser_capture"
SA_BROWSER_CAPTURE_SOURCE = "sa-bookmarklet"

# Yahoo recommendation key → normalized rating
_YF_RATING_MAP: Dict[str, str] = {
    "strong_buy": "strong buy",
    "buy": "buy",
    "hold": "hold",
    "underperform": "sell",
    "sell": "sell",
}

_BROWSER_RATING_MAP: Dict[str, str] = {
    "strong buy": "strong buy",
    "buy": "buy",
    "hold": "hold",
    "neutral": "hold",
    "sell": "sell",
    "strong sell": "strong sell",
    "very bullish": "very bullish",
    "bullish": "bullish",
    "bearish": "bearish",
    "very bearish": "very bearish",
}

_BROWSER_GRADE_KEYS: Dict[str, str] = {
    "value": "value_grade",
    "value_grade": "value_grade",
    "growth": "growth_grade",
    "growth_grade": "growth_grade",
    "momentum": "momentum_grade",
    "momentum_grade": "momentum_grade",
    "profitability": "profitability_grade",
    "profitability_grade": "profitability_grade",
    "revisions": "revisions_grade",
    "revisions_grade": "revisions_grade",
}


@dataclass(frozen=True)
class SABrowserCapture:
    """Structured payload captured from an authenticated browser page."""

    ticker: str
    snapshot: SAQuantSnapshot
    factor_grades: Dict[str, str]
    page_type: str = ""
    url: str = ""
    title: str = ""

    @property
    def has_quant_signal(self) -> bool:
        return bool(self.snapshot.rating or self.snapshot.quant_score_raw is not None)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "title": self.title,
            "url": self.url,
            "page_type": self.page_type,
            "captured_at": self.snapshot.updated_at,
            "rating": self.snapshot.rating,
            "quant_score": self.snapshot.quant_score_raw,
            "grades": dict(self.factor_grades),
            "raw_fields": dict(self.snapshot.raw_fields),
        }


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_rating(value: Any) -> str:
    clean = " ".join(str(value or "").strip().lower().split())
    if not clean:
        return ""
    return _BROWSER_RATING_MAP.get(clean, clean)


def _normalize_grade_value(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if re.fullmatch(r"[A-F][+-]?", text):
        return text
    return ""


def _normalize_factor_grades(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: Dict[str, str] = {}
    for key, value in raw.items():
        canonical = _BROWSER_GRADE_KEYS.get(str(key).strip().lower())
        if not canonical:
            continue
        grade = _normalize_grade_value(value)
        if grade:
            normalized[canonical] = grade
    return normalized


def _extract_ticker(payload: Mapping[str, Any]) -> str:
    candidates = [
        payload.get("ticker"),
        payload.get("symbol"),
        payload.get("primary_ticker"),
    ]
    tickers_raw = payload.get("tickers")
    if isinstance(tickers_raw, list) and tickers_raw:
        candidates.extend(tickers_raw)
    elif isinstance(tickers_raw, str):
        candidates.extend(tickers_raw.split(","))
    for candidate in candidates:
        symbol = re.sub(r"[^A-Z.=\-]", "", str(candidate or "").upper()).strip()
        if 0 < len(symbol) <= 12:
            return symbol
    url = str(payload.get("url") or "")
    match = re.search(r"/symbol/([A-Z.=\-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""


def _capture_timestamp(payload: Mapping[str, Any], fallback: str = "") -> str:
    raw = str(payload.get("captured_at") or payload.get("updated_at") or fallback or "").strip()
    if raw:
        return raw
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_sa_browser_payload(
    payload: Mapping[str, Any],
    captured_at: str = "",
) -> SABrowserCapture:
    """Normalize bookmarklet/browser payload into an SA snapshot."""
    symbol = _extract_ticker(payload)
    if not symbol:
        raise ValueError("ticker is required")

    grades = _normalize_factor_grades(payload.get("grades") or payload.get("factor_grades") or {})
    rating = _normalize_rating(
        payload.get("quant_rating")
        or payload.get("quantRating")
        or payload.get("rating")
        or payload.get("sa_rating")
    )
    quant_score = _coerce_float(
        payload.get("quant_score")
        or payload.get("quantScore")
        or payload.get("quant_score_raw")
        or payload.get("sa_quant_score")
    )
    if not rating and quant_score is None and not grades:
        raise ValueError("payload did not include SA quant fields")

    raw_fields = dict(payload.get("raw_fields") or {}) if isinstance(payload.get("raw_fields"), Mapping) else {}
    author_rating = _normalize_rating(payload.get("author_rating") or payload.get("sa_authors_rating"))
    wall_st_rating = _normalize_rating(payload.get("wall_st_rating") or payload.get("analyst_rating"))
    raw_fields.update(
        {
            "rating": rating,
            "quant_score_raw": quant_score,
            "sa_authors_rating": author_rating,
            "wall_st_rating": wall_st_rating,
            "factor_grades": dict(grades),
            "title": str(payload.get("title") or ""),
            "url": str(payload.get("url") or ""),
            "page_type": str(payload.get("page_type") or payload.get("pageType") or ""),
            "source": SA_BROWSER_CAPTURE_SOURCE,
        }
    )

    updated_at = _capture_timestamp(payload, fallback=captured_at)
    source_ref = str(payload.get("url") or payload.get("source_ref") or f"sa-browser-{symbol}").strip()
    snapshot = SAQuantSnapshot(
        ticker=symbol,
        rating=rating,
        quant_score_raw=quant_score,
        sector_rank=_coerce_float(payload.get("sector_rank")),
        industry_rank=_coerce_float(payload.get("industry_rank")),
        updated_at=updated_at,
        source_ref=source_ref,
        raw_fields=raw_fields,
    )
    return SABrowserCapture(
        ticker=symbol,
        snapshot=snapshot,
        factor_grades=grades,
        page_type=str(payload.get("page_type") or payload.get("pageType") or ""),
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
    )


class SABrowserCaptureAdapter:
    """Prefer recent browser-captured SA snapshots, then fall back to YF/Finnhub."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        max_age_seconds: int = 86400,
        fallback: Optional["YFinnhubAdapter"] = None,
    ):
        self.db_path = db_path
        self.max_age_seconds = max(300, int(max_age_seconds))
        self.fallback = fallback or YFinnhubAdapter()

    def fetch_snapshot(self, ticker: str) -> SAQuantSnapshot:
        capture = self._load_recent_capture(ticker)
        if capture and capture.has_quant_signal:
            return capture.snapshot
        return self.fallback.fetch_snapshot(ticker)

    def fetch_layer_score(self, ticker: str, as_of: str):
        capture = self._load_recent_capture(ticker)
        if capture and capture.has_quant_signal:
            return score_sa_quant_snapshot(
                snapshot=capture.snapshot,
                as_of=as_of,
                source="sa-browser-capture",
            )
        return self.fallback.fetch_layer_score(ticker, as_of)

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        capture = self._load_recent_capture(ticker)
        if capture and capture.factor_grades:
            return capture.factor_grades
        return self.fallback.fetch_factor_grades(ticker)

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        return self.fallback.fetch_news(ticker, count=count)

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        return self.fallback.fetch_analyst_recs(ticker)

    def close(self):
        self.fallback.close()

    def _load_recent_capture(self, ticker: str) -> Optional[SABrowserCapture]:
        symbol = ticker.strip().upper()
        if not symbol:
            return None

        conn = get_conn(self.db_path)
        rows = conn.execute(
            """SELECT payload, retrieved_at
               FROM research_events
               WHERE event_type=? AND source=? AND symbol=?
               ORDER BY retrieved_at DESC, created_at DESC
               LIMIT 10""",
            (SA_BROWSER_CAPTURE_EVENT_TYPE, SA_BROWSER_CAPTURE_SOURCE, symbol),
        ).fetchall()
        conn.close()

        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            try:
                capture = parse_sa_browser_payload(payload, captured_at=str(row["retrieved_at"] or ""))
            except ValueError:
                continue
            if self._is_fresh(capture.snapshot.updated_at):
                return capture
        return None

    def _is_fresh(self, timestamp: str) -> bool:
        raw = str(timestamp or "").strip()
        if not raw:
            return False
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= datetime.now(timezone.utc) - timedelta(seconds=self.max_age_seconds)


class YFinnhubAdapter:
    """Drop-in replacement for SAQuantClient using Yahoo Finance + Finnhub."""

    def __init__(self):
        self._finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()

    # ── Core interface (matches SAQuantClient) ────────────────────────

    def fetch_snapshot(self, ticker: str) -> SAQuantSnapshot:
        """Fetch Yahoo Finance info and return SAQuantSnapshot."""
        import yfinance as yf

        symbol = ticker.strip().upper()
        t = yf.Ticker(symbol)
        info = t.info or {}

        rating = _YF_RATING_MAP.get(
            info.get("recommendationKey", ""), ""
        )
        # Yahoo provides a recommendationMean on 1-5 scale (1=strong buy, 5=sell)
        raw_score = info.get("recommendationMean")
        quant_score = None
        if raw_score is not None:
            try:
                # Convert 1-5 to 0-100 (invert: 1=100, 5=0)
                quant_score = max(0.0, min(100.0, (5.0 - float(raw_score)) * 25.0))
            except (ValueError, TypeError):
                pass

        return SAQuantSnapshot(
            ticker=symbol,
            rating=rating,
            quant_score_raw=quant_score,
            sector_rank=None,
            industry_rank=None,
            updated_at=datetime.now(timezone.utc).isoformat(),
            source_ref=f"yf-finnhub-{symbol}",
            raw_fields={
                "rating": rating,
                "quant_score_raw": quant_score,
                "recommendation_key": info.get("recommendationKey", ""),
                "recommendation_mean": raw_score,
                "target_mean_price": info.get("targetMeanPrice"),
                "target_high_price": info.get("targetHighPrice"),
                "target_low_price": info.get("targetLowPrice"),
                "num_analysts": info.get("numberOfAnalystOpinions"),
                "source": "yahoo_finance+finnhub",
            },
        )

    def fetch_layer_score(self, ticker: str, as_of: str):
        """Fetch data and return L8 LayerScore."""
        snapshot = self.fetch_snapshot(ticker)

        # Enhance with Finnhub consensus if available
        if self._finnhub_key:
            fh = self._fetch_finnhub_consensus(ticker)
            if fh and not snapshot.rating:
                snapshot = SAQuantSnapshot(
                    ticker=snapshot.ticker,
                    rating=fh.get("rating", snapshot.rating),
                    quant_score_raw=fh.get("score", snapshot.quant_score_raw),
                    sector_rank=snapshot.sector_rank,
                    industry_rank=snapshot.industry_rank,
                    updated_at=snapshot.updated_at,
                    source_ref=snapshot.source_ref,
                    raw_fields={**snapshot.raw_fields, "finnhub": fh},
                )

        return score_sa_quant_snapshot(
            snapshot=snapshot, as_of=as_of, source="yf-finnhub",
        )

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        """Yahoo doesn't have SA-style factor grades — return empty."""
        return {}

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        """Fetch news via yfinance."""
        import yfinance as yf

        symbol = ticker.strip().upper()
        articles: List[Dict[str, Any]] = []
        try:
            t = yf.Ticker(symbol)
            news = t.news or []
            for item in news[:count]:
                content = item.get("content", item)
                if isinstance(content, dict):
                    title = content.get("title", "")
                    pub_date = content.get("pubDate", content.get("providerPublishTime", ""))
                    url = content.get("canonicalUrl", {})
                    link = url.get("url", "") if isinstance(url, dict) else str(url)
                else:
                    title = item.get("title", "")
                    pub_date = str(item.get("providerPublishTime", ""))
                    link = item.get("link", "")
                if title:
                    articles.append({
                        "headline": str(title),
                        "published_at": str(pub_date),
                        "source": "yahoo_finance",
                        "url": str(link),
                    })
        except Exception as exc:
            logger.warning("YF news fetch failed for %s: %s", ticker, exc)
        return articles

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch analyst recommendations from Finnhub."""
        if not self._finnhub_key:
            return self._yf_analyst_recs(ticker)

        import requests
        symbol = ticker.strip().upper()
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": symbol, "token": self._finnhub_key},
                timeout=10,
            )
            if r.status_code != 200:
                return self._yf_analyst_recs(ticker)

            data = r.json()
            recs: List[Dict[str, Any]] = []
            for period in data[:6]:
                total = sum(period.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
                if total == 0:
                    continue
                # Derive consensus rating from majority
                best_key = max(("strongBuy", "buy", "hold", "sell", "strongSell"),
                               key=lambda k: period.get(k, 0))
                rating_map = {"strongBuy": "Strong Buy", "buy": "Buy",
                              "hold": "Hold", "sell": "Sell", "strongSell": "Strong Sell"}
                recs.append({
                    "analyst": "consensus",
                    "rating": rating_map.get(best_key, ""),
                    "target_price": None,
                    "date": period.get("period", ""),
                    "breakdown": {k: period.get(k, 0) for k in
                                  ("strongBuy", "buy", "hold", "sell", "strongSell")},
                })
            return recs
        except Exception as exc:
            logger.warning("Finnhub recs failed for %s: %s", ticker, exc)
            return self._yf_analyst_recs(ticker)

    # ── Helpers ───────────────────────────────────────────────────────

    def _fetch_finnhub_consensus(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch latest Finnhub consensus and convert to rating + score."""
        import requests

        symbol = ticker.strip().upper()
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": symbol, "token": self._finnhub_key},
                timeout=10,
            )
            if r.status_code != 200:
                return None

            data = r.json()
            if not data:
                return None

            latest = data[0]
            sb = latest.get("strongBuy", 0)
            b = latest.get("buy", 0)
            h = latest.get("hold", 0)
            s = latest.get("sell", 0)
            ss = latest.get("strongSell", 0)
            total = sb + b + h + s + ss
            if total == 0:
                return None

            # Weighted score: strongBuy=100, buy=75, hold=50, sell=25, strongSell=0
            weighted = (sb * 100 + b * 75 + h * 50 + s * 25 + ss * 0) / total

            if weighted >= 80:
                rating = "strong buy"
            elif weighted >= 65:
                rating = "buy"
            elif weighted >= 45:
                rating = "hold"
            elif weighted >= 30:
                rating = "sell"
            else:
                rating = "strong sell"

            return {
                "rating": rating,
                "score": round(weighted, 2),
                "period": latest.get("period", ""),
                "breakdown": {"strongBuy": sb, "buy": b, "hold": h, "sell": s, "strongSell": ss},
            }
        except Exception as exc:
            logger.warning("Finnhub consensus failed for %s: %s", ticker, exc)
            return None

    @staticmethod
    def _yf_analyst_recs(ticker: str) -> List[Dict[str, Any]]:
        """Fallback: get recommendations from yfinance."""
        import yfinance as yf

        recs: List[Dict[str, Any]] = []
        try:
            t = yf.Ticker(ticker.strip().upper())
            df = t.recommendations
            if df is not None and len(df) > 0:
                for _, row in df.tail(3).iterrows():
                    total = sum(row.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
                    if total == 0:
                        continue
                    best_key = max(("strongBuy", "buy", "hold", "sell", "strongSell"),
                                   key=lambda k: row.get(k, 0))
                    rating_map = {"strongBuy": "Strong Buy", "buy": "Buy",
                                  "hold": "Hold", "sell": "Sell", "strongSell": "Strong Sell"}
                    recs.append({
                        "analyst": "consensus",
                        "rating": rating_map.get(best_key, ""),
                        "target_price": None,
                        "date": str(row.get("period", row.name if hasattr(row, "name") else "")),
                    })
        except Exception:
            pass
        return recs

    def close(self):
        pass
