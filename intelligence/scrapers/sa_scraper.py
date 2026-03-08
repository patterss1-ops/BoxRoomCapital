"""Seeking Alpha hybrid scraper — Playwright login + internal API.

Strategy:
1. Use Playwright + stealth to log in and obtain session cookies
2. Use requests with those cookies to call SA's internal JSON API
3. Cache cookies for reuse (they last ~30 min)

Requires SA_EMAIL and SA_PASSWORD env vars (Replit secrets).
"""

from __future__ import annotations

import logging
import os
import re
import time
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

import requests as req_lib

from intelligence.scrapers import NUMERIC_TO_GRADE, RATING_MAP

logger = logging.getLogger(__name__)

# SA internal API base
_API_BASE = "https://seekingalpha.com/api/v3"

_NUMERIC_TO_GRADE = NUMERIC_TO_GRADE

_GRADE_TO_SCORE: Dict[str, float] = {
    "A+": 100.0, "A": 95.0, "A-": 90.0,
    "B+": 85.0, "B": 80.0, "B-": 75.0,
    "C+": 70.0, "C": 65.0, "C-": 60.0,
    "D+": 55.0, "D": 50.0, "D-": 45.0,
    "F": 20.0,
}

_RATING_MAP = RATING_MAP

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class SAScraperConfig:
    """Configuration for the Seeking Alpha hybrid scraper."""
    email: str = ""
    password: str = ""
    timeout_seconds: float = 15.0
    browser_timeout_ms: int = 30000
    request_delay: float = 2.0  # seconds between API calls
    chromium_path: str = ""  # optional path to Chromium executable
    source: str = "sa-scraper"


@dataclass(frozen=True)
class SAScrapedSnapshot:
    """Data scraped from SA for a single ticker."""
    ticker: str
    quant_rating: str = ""
    quant_score: Optional[float] = None
    sa_authors_rating: str = ""
    wall_st_rating: str = ""
    factor_grades: Dict[str, str] = field(default_factory=dict)
    source: str = "sa-scraper"


class SAScraperError(RuntimeError):
    """Raised when SA scraping fails."""
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class SAScraper:
    """Hybrid Playwright-login + internal-API scraper for Seeking Alpha."""

    def __init__(self, config: Optional[SAScraperConfig] = None):
        cfg = config or SAScraperConfig()
        self.config = SAScraperConfig(
            email=cfg.email or os.getenv("SEEKING_ALPHA_EMAIL", os.getenv("SA_EMAIL", "")),
            password=cfg.password or os.getenv("SEEKING_ALPHA_PASSWORD", os.getenv("SA_PASSWORD", "")),
            timeout_seconds=cfg.timeout_seconds,
            browser_timeout_ms=cfg.browser_timeout_ms,
            request_delay=cfg.request_delay,
            chromium_path=cfg.chromium_path or os.getenv("SA_CHROMIUM_PATH", ""),
            source=cfg.source,
        )
        self._session: Optional[req_lib.Session] = None
        self._cookies_obtained_at: float = 0
        self._browser = None

    # ── Chromium discovery ─────────────────────────────────────────────

    @staticmethod
    def _find_chromium() -> str:
        """Auto-detect Chromium binary on Replit (nix store)."""
        import glob
        candidates = sorted(glob.glob(
            "/nix/store/*-playwright-browsers-*/chromium-*/chrome-linux/chrome"
        ))
        if candidates:
            return candidates[-1]  # newest version
        return ""

    # ── Session management ────────────────────────────────────────────

    def _needs_login(self) -> bool:
        """Cookies expire after ~25 min — refresh proactively."""
        if self._session is None:
            return True
        age = time.monotonic() - self._cookies_obtained_at
        return age > 1500  # 25 minutes

    def _obtain_cookies(self) -> Dict[str, str]:
        """Use Playwright + stealth to log in and extract cookies."""
        if not self.config.email or not self.config.password:
            raise SAScraperError("SA_EMAIL and SA_PASSWORD required", retryable=False)

        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import Stealth
        except ImportError as exc:
            raise SAScraperError(
                "playwright/playwright-stealth not installed", retryable=False
            ) from exc

        pw = sync_playwright().start()
        try:
            launch_kwargs: Dict[str, Any] = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            chrome_path = self.config.chromium_path or self._find_chromium()
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path
            browser = pw.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            stealth = Stealth()
            stealth.apply(context)
            page = context.new_page()

            # Navigate to login
            page.goto(
                "https://seekingalpha.com/login",
                timeout=self.config.browser_timeout_ms,
            )
            page.wait_for_load_state("networkidle", timeout=self.config.browser_timeout_ms)
            time.sleep(random.uniform(1.5, 3.0))

            # Fill login form
            email_sel = 'input[data-test-id="email-input"], input[name="email"], input[type="email"]'
            pwd_sel = 'input[data-test-id="password-input"], input[name="password"], input[type="password"]'
            submit_sel = 'button[data-test-id="login-button"], button[type="submit"]'

            page.wait_for_selector(email_sel, timeout=self.config.browser_timeout_ms)
            time.sleep(random.uniform(0.5, 1.0))
            page.fill(email_sel, self.config.email)
            time.sleep(random.uniform(0.3, 0.8))
            page.fill(pwd_sel, self.config.password)
            time.sleep(random.uniform(0.5, 1.5))
            page.click(submit_sel)
            page.wait_for_load_state("networkidle", timeout=self.config.browser_timeout_ms)
            time.sleep(random.uniform(2.0, 4.0))

            # Extract cookies
            raw_cookies = context.cookies()
            cookies = {c["name"]: c["value"] for c in raw_cookies}
            logger.info("SA login succeeded — got %d cookies", len(cookies))

            browser.close()
            return cookies
        finally:
            pw.stop()

    def _ensure_session(self):
        """Ensure we have a valid requests session with SA cookies."""
        if not self._needs_login():
            return

        cookies = self._obtain_cookies()
        self._session = req_lib.Session()
        self._session.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://seekingalpha.com/",
            "Origin": "https://seekingalpha.com",
        })
        for name, value in cookies.items():
            self._session.cookies.set(name, value)
        self._cookies_obtained_at = time.monotonic()

    def _api_get(self, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        """Make an authenticated GET to SA's internal API."""
        self._ensure_session()
        assert self._session is not None

        url = f"{_API_BASE}/{path.lstrip('/')}"
        time.sleep(random.uniform(self.config.request_delay * 0.8,
                                  self.config.request_delay * 1.2))

        resp = self._session.get(url, params=params, timeout=self.config.timeout_seconds)

        if resp.status_code == 403:
            # Session expired or blocked — force re-login on next call
            logger.warning("SA API returned 403 — session may be expired")
            self._session = None
            raise SAScraperError("SA API 403 — session expired", retryable=True)

        if resp.status_code != 200:
            raise SAScraperError(
                f"SA API HTTP {resp.status_code} for {path}", retryable=resp.status_code >= 500
            )

        return resp.json()

    def _delay(self):
        """Human-like delay between operations."""
        time.sleep(random.uniform(self.config.request_delay, self.config.request_delay * 2))

    # ── Quant ratings + factor grades ─────────────────────────────────

    def fetch_snapshot(self, ticker: str) -> SAScrapedSnapshot:
        """Fetch quant rating, scores, and factor grades for a ticker."""
        symbol = ticker.strip().upper()
        slug = symbol.lower()
        if not symbol:
            raise SAScraperError("ticker is required", retryable=False)

        quant_rating = ""
        quant_score = None
        sa_authors_rating = ""
        wall_st_rating = ""
        factor_grades: Dict[str, str] = {}

        # 1. Fetch factor grades via metric_grades endpoint
        try:
            grades_data = self._api_get(
                "ticker_metric_grades",
                params={"filter[slugs]": slug},
            )
            factor_grades = self._parse_factor_grades(grades_data)
        except Exception as exc:
            logger.warning("SA factor grades failed for %s: %s", symbol, exc)

        self._delay()

        # 2. Fetch quant/author/wall-st ratings via the ratings summary
        try:
            # Try the symbol ratings endpoint
            ratings_data = self._api_get(
                f"symbols/{slug}/rating/summary",
            )
            quant_rating, quant_score, sa_authors_rating, wall_st_rating = (
                self._parse_ratings(ratings_data)
            )
        except Exception as exc:
            logger.warning("SA ratings failed for %s: %s", symbol, exc)

        # 3. If quant rating is still empty, derive from factor grades
        if not quant_rating and factor_grades:
            quant_rating, quant_score = self._derive_rating_from_grades(factor_grades)

        return SAScrapedSnapshot(
            ticker=symbol,
            quant_rating=quant_rating,
            quant_score=quant_score,
            sa_authors_rating=sa_authors_rating,
            wall_st_rating=wall_st_rating,
            factor_grades=factor_grades,
            source=self.config.source,
        )

    def _parse_factor_grades(self, data: Any) -> Dict[str, str]:
        """Parse factor grades from API response."""
        grades: Dict[str, str] = {}
        if not isinstance(data, Mapping):
            return grades

        # Handle different response shapes
        items = data.get("metrics_grades") or data.get("data") or []
        if isinstance(items, Mapping):
            items = [items]
        if not isinstance(items, list):
            return grades

        grade_fields = {
            "value_category": "value_grade",
            "growth_category": "growth_grade",
            "profitability_category": "profitability_grade",
            "momentum_category": "momentum_grade",
            "eps_revisions_category": "revisions_grade",
            "revisions_category": "revisions_grade",
        }

        for item in items:
            if not isinstance(item, Mapping):
                # Could be nested in attributes (JSON:API)
                continue
            attrs = item.get("attributes", item) if isinstance(item, Mapping) else item
            if not isinstance(attrs, Mapping):
                continue

            for api_key, grade_key in grade_fields.items():
                val = attrs.get(api_key)
                if val is not None and grade_key not in grades:
                    numeric = int(val) if isinstance(val, (int, float)) else None
                    if numeric and numeric in _NUMERIC_TO_GRADE:
                        grades[grade_key] = _NUMERIC_TO_GRADE[numeric]
            if grades:
                break

        return grades

    def _parse_ratings(self, data: Any):
        """Parse quant/author/wall-st ratings from API response."""
        quant_rating = ""
        quant_score = None
        sa_authors_rating = ""
        wall_st_rating = ""

        if not isinstance(data, Mapping):
            return quant_rating, quant_score, sa_authors_rating, wall_st_rating

        # Navigate various response shapes
        items = data.get("data", data)
        if isinstance(items, list):
            items = items[0] if items else {}
        attrs = items.get("attributes", items) if isinstance(items, Mapping) else {}
        if not isinstance(attrs, Mapping):
            return quant_rating, quant_score, sa_authors_rating, wall_st_rating

        # Quant rating
        for key in ("quantRating", "quant_rating", "quantRecommendation"):
            val = attrs.get(key)
            if val:
                quant_rating = self._normalize_rating(str(val))
                break

        # Quant score
        for key in ("quantScore", "quant_score", "quant_rating_score"):
            val = attrs.get(key)
            if val is not None:
                try:
                    quant_score = float(val)
                    break
                except (ValueError, TypeError):
                    pass

        # SA authors
        for key in ("authorsRating", "authors_rating", "saAuthorsRating"):
            val = attrs.get(key)
            if val:
                sa_authors_rating = self._normalize_rating(str(val))
                break

        # Wall Street
        for key in ("wallStreetRating", "wall_street_rating", "sellSideRating"):
            val = attrs.get(key)
            if val:
                wall_st_rating = self._normalize_rating(str(val))
                break

        return quant_rating, quant_score, sa_authors_rating, wall_st_rating

    def _derive_rating_from_grades(self, grades: Dict[str, str]):
        """Derive an approximate quant rating from factor grades."""
        scores = [_GRADE_TO_SCORE[g] for g in grades.values() if g in _GRADE_TO_SCORE]
        if not scores:
            return "", None

        avg = sum(scores) / len(scores)
        # Map average score to a rating
        if avg >= 85:
            return "strong buy", avg / 20.0
        elif avg >= 70:
            return "buy", avg / 20.0
        elif avg >= 55:
            return "hold", avg / 20.0
        elif avg >= 40:
            return "sell", avg / 20.0
        else:
            return "strong sell", avg / 20.0

    def _normalize_rating(self, text: str) -> str:
        cleaned = " ".join(text.strip().lower().split())
        return _RATING_MAP.get(cleaned, cleaned)

    # ── Factor grades convenience ─────────────────────────────────────

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        """Fetch factor grades for a ticker."""
        snapshot = self.fetch_snapshot(ticker)
        return dict(snapshot.factor_grades)

    # ── News ──────────────────────────────────────────────────────────

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        """Fetch recent news/analysis for a ticker via SA internal API."""
        slug = ticker.strip().lower()
        if not slug:
            return []

        try:
            data = self._api_get(
                "news",
                params={
                    "filter[category]": "market-news::all",
                    "filter[slugs]": slug,
                    "page[size]": str(min(count, 40)),
                    "include": "author,primaryTickers",
                },
            )
            return self._parse_news(data, count)
        except Exception as exc:
            logger.warning("SA news fetch failed for %s: %s", ticker, exc)
            return []

    def _parse_news(self, data: Any, limit: int) -> List[Dict[str, Any]]:
        """Parse news articles from JSON:API response."""
        articles: List[Dict[str, Any]] = []
        if not isinstance(data, Mapping):
            return articles

        items = data.get("data", [])
        if not isinstance(items, list):
            return articles

        for item in items[:limit]:
            if not isinstance(item, Mapping):
                continue
            attrs = item.get("attributes", {})
            if not isinstance(attrs, Mapping):
                continue

            headline = str(attrs.get("title") or attrs.get("headline") or "").strip()
            if not headline:
                continue

            published = str(
                attrs.get("publishOn") or attrs.get("published_at") or ""
            ).strip()

            link = ""
            links = item.get("links", {})
            if isinstance(links, Mapping):
                link = str(links.get("canonical") or links.get("self") or "")
            if link and not link.startswith("http"):
                link = f"https://seekingalpha.com{link}"

            articles.append({
                "headline": headline,
                "published_at": published,
                "source": "seeking_alpha",
                "url": link,
            })

        return articles

    # ── Analyst recommendations ───────────────────────────────────────

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch sell-side analyst recommendations."""
        slug = ticker.strip().lower()
        if not slug:
            return []

        try:
            data = self._api_get(
                f"symbols/{slug}/rating/sell_side_ratings",
            )
            return self._parse_analyst_recs(data)
        except Exception as exc:
            logger.warning("SA analyst recs failed for %s: %s", ticker, exc)
            return []

    def _parse_analyst_recs(self, data: Any) -> List[Dict[str, Any]]:
        """Parse analyst recommendations from API response."""
        recs: List[Dict[str, Any]] = []
        if not isinstance(data, Mapping):
            return recs

        items = data.get("data", [])
        if not isinstance(items, list):
            items = [data] if "analyst" in str(data) else []

        for item in items:
            if not isinstance(item, Mapping):
                continue
            attrs = item.get("attributes", item)
            if not isinstance(attrs, Mapping):
                continue

            analyst = str(attrs.get("analyst") or attrs.get("firm") or "").strip()
            rating = str(attrs.get("rating") or attrs.get("recommendation") or "").strip()
            target = attrs.get("target_price") or attrs.get("priceTarget")
            date_val = str(attrs.get("date") or attrs.get("publishedAt") or "").strip()

            target_price = None
            if target is not None:
                try:
                    target_price = float(target)
                except (ValueError, TypeError):
                    pass

            if analyst or rating:
                recs.append({
                    "analyst": analyst,
                    "rating": rating,
                    "target_price": target_price,
                    "date": date_val,
                })

        return recs

    # ── Cleanup ───────────────────────────────────────────────────────

    def close(self):
        """Clean up resources."""
        if self._session:
            self._session.close()
        self._session = None
        self._cookies_obtained_at = 0
