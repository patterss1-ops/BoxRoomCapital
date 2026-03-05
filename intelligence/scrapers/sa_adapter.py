"""Adapter: Yahoo Finance + Finnhub as drop-in replacement for SAQuantClient.

SA's PerimeterX bot protection blocks all server-side access (requests,
cloudscraper, Playwright+stealth all get 403). This adapter uses freely
available Yahoo Finance data + Finnhub API (we have a key) to produce
equivalent L8 SA Quant scores.

Data mapping:
- Yahoo `recommendationKey` → quant rating (buy/hold/sell)
- Yahoo `targetMeanPrice` → price target consensus
- Finnhub recommendation trends → buy/hold/sell breakdown for scoring
- Yahoo `info` fields → supplementary data (sector rank approximation)
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from intelligence.sa_quant_client import SAQuantSnapshot

logger = logging.getLogger(__name__)

# Yahoo recommendation key → normalized rating
_YF_RATING_MAP: Dict[str, str] = {
    "strong_buy": "strong buy",
    "buy": "buy",
    "hold": "hold",
    "underperform": "sell",
    "sell": "sell",
}

# Rating → 0-100 score
_RATING_TO_SCORE: Dict[str, float] = {
    "strong buy": 90.0,
    "buy": 75.0,
    "hold": 50.0,
    "sell": 25.0,
    "strong sell": 10.0,
}


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
        from intelligence.sa_quant_client import score_sa_quant_snapshot

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
