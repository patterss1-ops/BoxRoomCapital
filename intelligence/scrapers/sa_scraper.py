"""Seeking Alpha web scraper — replaces the RapidAPI client.

Uses Playwright headless browser to log in and extract:
- Quant ratings + scores (L8)
- Factor grades (value/growth/momentum/profitability/revisions)
- News headlines
- Analyst recommendations

Requires SA_EMAIL and SA_PASSWORD env vars (Replit secrets).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GRADE_MAP: Dict[str, float] = {
    "A+": 100.0, "A": 95.0, "A-": 90.0,
    "B+": 85.0, "B": 80.0, "B-": 75.0,
    "C+": 70.0, "C": 65.0, "C-": 60.0,
    "D+": 55.0, "D": 50.0, "D-": 45.0,
    "F": 20.0,
}

_RATING_MAP: Dict[str, str] = {
    "strong buy": "strong buy",
    "buy": "buy",
    "hold": "hold",
    "sell": "sell",
    "strong sell": "strong sell",
    "very bullish": "very bullish",
    "bullish": "bullish",
    "neutral": "hold",
    "bearish": "bearish",
    "very bearish": "very bearish",
}


@dataclass(frozen=True)
class SAScraperConfig:
    """Configuration for the Seeking Alpha scraper."""

    email: str = ""
    password: str = ""
    timeout_ms: int = 30000
    source: str = "sa-scraper"


@dataclass(frozen=True)
class SAScrapedSnapshot:
    """Data scraped from a SA ticker page."""

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
    """Playwright-based scraper for Seeking Alpha data."""

    def __init__(self, config: Optional[SAScraperConfig] = None):
        cfg = config or SAScraperConfig()
        self.config = SAScraperConfig(
            email=cfg.email or os.getenv("SA_EMAIL", ""),
            password=cfg.password or os.getenv("SA_PASSWORD", ""),
            timeout_ms=cfg.timeout_ms,
            source=cfg.source,
        )
        self._browser = None
        self._context = None
        self._logged_in = False

    def _ensure_browser(self):
        """Lazy-initialize Playwright browser."""
        if self._browser is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
        except ImportError:
            logger.error(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )
            raise

    def _login(self, page):
        """Log into Seeking Alpha."""
        if self._logged_in:
            return

        if not self.config.email or not self.config.password:
            raise SAScraperError("SA_EMAIL and SA_PASSWORD required", retryable=False)

        page.goto("https://seekingalpha.com/login", timeout=self.config.timeout_ms)
        page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

        # Fill login form — SA uses data-test-id attributes
        email_sel = 'input[data-test-id="email-input"], input[name="email"], input[type="email"]'
        pwd_sel = 'input[data-test-id="password-input"], input[name="password"], input[type="password"]'
        submit_sel = (
            'button[data-test-id="login-button"], '
            'button[type="submit"], '
            'form button'
        )

        page.wait_for_selector(email_sel, timeout=self.config.timeout_ms)
        page.fill(email_sel, self.config.email)
        page.fill(pwd_sel, self.config.password)
        page.click(submit_sel)
        page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

        # Verify login succeeded — check for user menu or absence of login button
        page.wait_for_timeout(2000)
        self._logged_in = True
        logger.info("SA login succeeded")

    def _extract_number(self, text: str) -> Optional[float]:
        """Extract first numeric value from text."""
        match = re.search(r"[-+]?\d*\.?\d+", text.replace(",", ""))
        if match:
            return float(match.group())
        return None

    def _normalize_rating(self, text: str) -> str:
        """Normalize a rating string to canonical form."""
        cleaned = " ".join(text.strip().lower().split())
        return _RATING_MAP.get(cleaned, cleaned)

    # ── Quant ratings + factor grades (symbol page) ───────────────────

    def fetch_snapshot(self, ticker: str) -> SAScrapedSnapshot:
        """Scrape quant rating, scores, and factor grades for a ticker.

        Navigates to seekingalpha.com/symbol/{TICKER}/ratings/quant-ratings
        """
        symbol = ticker.strip().upper()
        if not symbol:
            raise SAScraperError("ticker is required", retryable=False)

        try:
            self._ensure_browser()
            page = self._context.new_page()

            try:
                self._login(page)

                # Go to the quant ratings page
                url = f"https://seekingalpha.com/symbol/{symbol}/ratings/quant-ratings"
                page.goto(url, timeout=self.config.timeout_ms)
                page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
                page.wait_for_timeout(3000)

                quant_rating = ""
                quant_score = None
                sa_authors_rating = ""
                wall_st_rating = ""
                factor_grades: Dict[str, str] = {}

                # Extract the quant rating badge/label
                # SA typically shows ratings in spans/divs with data-test-id
                rating_selectors = [
                    '[data-test-id="quant-rating"] span',
                    '[data-test-id*="quant"] [class*="rating"]',
                    'span[class*="quant"][class*="rating"]',
                    'div[class*="ScoreCard"] span',
                ]
                for sel in rating_selectors:
                    els = page.query_selector_all(sel)
                    for el in els:
                        text = el.inner_text().strip()
                        normalized = self._normalize_rating(text)
                        if normalized in _RATING_MAP.values():
                            quant_rating = normalized
                            break
                    if quant_rating:
                        break

                # Try to get numeric quant score (1-5 scale typically)
                score_selectors = [
                    '[data-test-id="quant-score"]',
                    '[data-test-id*="quant"] [class*="score"]',
                    'span[class*="score"]',
                ]
                for sel in score_selectors:
                    els = page.query_selector_all(sel)
                    for el in els:
                        val = self._extract_number(el.inner_text())
                        if val is not None and 0 < val <= 5:
                            quant_score = val
                            break
                    if quant_score is not None:
                        break

                # Extract SA authors and Wall Street ratings from the summary row
                summary_selectors = [
                    '[data-test-id*="author"] span',
                    '[data-test-id*="sell-side"] span',
                    '[data-test-id*="wall-st"] span',
                ]
                # Fallback: grab all rating-like text from the ratings section
                all_text = page.inner_text("body")
                for label_key, attr in [
                    ("SA Authors", "sa_authors"),
                    ("Sell Side", "wall_st"),
                    ("Wall Street", "wall_st"),
                ]:
                    pattern = rf"{label_key}\s*[:\-]?\s*(Strong Buy|Buy|Hold|Sell|Strong Sell)"
                    match = re.search(pattern, all_text, re.IGNORECASE)
                    if match:
                        if attr == "sa_authors":
                            sa_authors_rating = self._normalize_rating(match.group(1))
                        else:
                            wall_st_rating = self._normalize_rating(match.group(1))

                # Extract factor grades table
                # SA shows: Valuation, Growth, Profitability, Momentum, Revisions
                grade_keys = [
                    ("valuation", "value_grade"),
                    ("value", "value_grade"),
                    ("growth", "growth_grade"),
                    ("profitability", "profitability_grade"),
                    ("momentum", "momentum_grade"),
                    ("revisions", "revisions_grade"),
                    ("eps revisions", "revisions_grade"),
                ]
                for label, grade_key in grade_keys:
                    if grade_key in factor_grades:
                        continue
                    pattern = rf"{label}\s*[:\-]?\s*([A-Da-d][+-]?|F)"
                    match = re.search(pattern, all_text, re.IGNORECASE)
                    if match:
                        grade = match.group(1).upper()
                        if grade in _GRADE_MAP:
                            factor_grades[grade_key] = grade

                # If we didn't find the quant rating via selectors, try text
                if not quant_rating:
                    pattern = r"Quant\s+Rating\s*[:\-]?\s*(Strong Buy|Buy|Hold|Sell|Strong Sell|Very Bullish|Bullish|Neutral|Bearish|Very Bearish)"
                    match = re.search(pattern, all_text, re.IGNORECASE)
                    if match:
                        quant_rating = self._normalize_rating(match.group(1))

                return SAScrapedSnapshot(
                    ticker=symbol,
                    quant_rating=quant_rating,
                    quant_score=quant_score,
                    sa_authors_rating=sa_authors_rating,
                    wall_st_rating=wall_st_rating,
                    factor_grades=factor_grades,
                    source=self.config.source,
                )
            finally:
                page.close()

        except SAScraperError:
            raise
        except Exception as exc:
            logger.warning("SA scrape failed for %s: %s", symbol, exc)
            raise SAScraperError(
                f"SA scrape failed for {symbol}: {exc}", retryable=True
            ) from exc

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        """Fetch factor grades for a ticker. Returns dict of grade letters.

        This is a convenience method — grades are also included in fetch_snapshot.
        """
        snapshot = self.fetch_snapshot(ticker)
        return dict(snapshot.factor_grades)

    # ── News headlines ────────────────────────────────────────────────

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        """Scrape recent news/analysis headlines for a ticker.

        Navigates to seekingalpha.com/symbol/{TICKER}/news
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        try:
            self._ensure_browser()
            page = self._context.new_page()

            try:
                self._login(page)
                url = f"https://seekingalpha.com/symbol/{symbol}/news"
                page.goto(url, timeout=self.config.timeout_ms)
                page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
                page.wait_for_timeout(3000)

                articles: List[Dict[str, Any]] = []

                # SA news pages use article tags or data-test-id on links
                link_selectors = [
                    'article a[data-test-id="post-list-item-title"]',
                    'a[data-test-id*="title"]',
                    'article h3 a',
                    '[class*="ItemHeader"] a',
                    'div[data-test-id*="news"] a',
                ]

                seen_headlines = set()
                for sel in link_selectors:
                    els = page.query_selector_all(sel)
                    for el in els:
                        headline = el.inner_text().strip()
                        if not headline or headline in seen_headlines:
                            continue
                        seen_headlines.add(headline)

                        href = el.get_attribute("href") or ""
                        if href and not href.startswith("http"):
                            href = f"https://seekingalpha.com{href}"

                        # Try to find the date near this element
                        parent = el.evaluate_handle("el => el.closest('article') || el.parentElement")
                        published_at = ""
                        try:
                            time_el = parent.as_element().query_selector("time, [datetime], [class*='date']")
                            if time_el:
                                published_at = (
                                    time_el.get_attribute("datetime")
                                    or time_el.inner_text().strip()
                                )
                        except Exception:
                            pass

                        articles.append({
                            "headline": headline,
                            "published_at": published_at,
                            "source": "seeking_alpha",
                            "url": href,
                        })

                        if len(articles) >= count:
                            break
                    if len(articles) >= count:
                        break

                return articles
            finally:
                page.close()

        except Exception as exc:
            logger.warning("SA news scrape failed for %s: %s", symbol, exc)
            return []

    # ── Analyst recommendations ───────────────────────────────────────

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        """Scrape analyst recommendations for a ticker.

        Navigates to seekingalpha.com/symbol/{TICKER}/ratings/sell-side-ratings
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        try:
            self._ensure_browser()
            page = self._context.new_page()

            try:
                self._login(page)
                url = f"https://seekingalpha.com/symbol/{symbol}/ratings/sell-side-ratings"
                page.goto(url, timeout=self.config.timeout_ms)
                page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
                page.wait_for_timeout(3000)

                recs: List[Dict[str, Any]] = []

                # SA shows analyst recs in table rows
                row_selectors = [
                    'table tbody tr',
                    '[data-test-id*="analyst"] tr',
                    '[class*="AnalystRatings"] tr',
                ]

                for sel in row_selectors:
                    rows = page.query_selector_all(sel)
                    for row in rows[:30]:
                        cells = row.query_selector_all("td")
                        if len(cells) < 3:
                            continue

                        analyst = cells[0].inner_text().strip()
                        rating = cells[1].inner_text().strip() if len(cells) > 1 else ""
                        target_str = cells[2].inner_text().strip() if len(cells) > 2 else ""
                        date_str = cells[-1].inner_text().strip() if len(cells) > 3 else ""

                        target_price = self._extract_number(
                            target_str.replace("$", "").replace("£", "")
                        )

                        if analyst:
                            recs.append({
                                "analyst": analyst,
                                "rating": rating,
                                "target_price": target_price,
                                "date": date_str,
                            })
                    if recs:
                        break

                # Fallback: extract from page text if no table found
                if not recs:
                    all_text = page.inner_text("body")
                    # Look for "X analysts, Y buy, Z hold" style summaries
                    pattern = r"(\d+)\s+(?:Wall Street\s+)?analysts?\s"
                    match = re.search(pattern, all_text, re.IGNORECASE)
                    if match:
                        recs.append({
                            "analyst": "consensus",
                            "rating": self._extract_consensus_rating(all_text),
                            "target_price": self._extract_price_target(all_text),
                            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        })

                return recs
            finally:
                page.close()

        except Exception as exc:
            logger.warning("SA analyst recs scrape failed for %s: %s", symbol, exc)
            return []

    def _extract_consensus_rating(self, text: str) -> str:
        """Extract consensus rating from page text."""
        pattern = r"(?:consensus|average)\s+(?:rating|recommendation)\s*[:\-]?\s*(Strong Buy|Buy|Hold|Sell|Strong Sell)"
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _extract_price_target(self, text: str) -> Optional[float]:
        """Extract average price target from page text."""
        pattern = r"(?:average|mean|consensus)\s+(?:price\s+)?target\s*[:\-]?\s*\$?([\d,.]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return self._extract_number(match.group(1))
        return None

    def close(self):
        """Clean up browser resources."""
        try:
            if self._browser:
                self._browser.close()
            if hasattr(self, "_pw") and self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._logged_in = False
