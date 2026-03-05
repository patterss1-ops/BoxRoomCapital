"""Koyfin web scraper for earnings estimates and fundamentals.

Uses Playwright headless browser to log in and extract data from Koyfin's
React-based web app. Requires KOYFIN_EMAIL and KOYFIN_PASSWORD env vars.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from intelligence.feature_store import FeatureRecord, FeatureStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KoyfinConfig:
    """Configuration for Koyfin scraper."""

    email: str = ""
    password: str = ""
    timeout_ms: int = 30000
    source: str = "koyfin-scraper"


class KoyfinScraper:
    """Playwright-based scraper for Koyfin financial data."""

    def __init__(self, config: Optional[KoyfinConfig] = None):
        cfg = config or KoyfinConfig()
        self.config = KoyfinConfig(
            email=cfg.email or os.getenv("KOYFIN_EMAIL", ""),
            password=cfg.password or os.getenv("KOYFIN_PASSWORD", ""),
            timeout_ms=cfg.timeout_ms,
            source=cfg.source,
        )
        self._browser = None
        self._context = None

    def _ensure_browser(self):
        """Lazy-initialize Playwright browser."""
        if self._browser is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context()
        except ImportError:
            logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
            raise
        except Exception as exc:
            logger.error("Failed to launch browser: %s", exc)
            raise

    def _login(self, page):
        """Log into Koyfin."""
        if not self.config.email or not self.config.password:
            raise ValueError("KOYFIN_EMAIL and KOYFIN_PASSWORD required")

        page.goto("https://app.koyfin.com/login", timeout=self.config.timeout_ms)
        page.fill('input[name="email"], input[type="email"]', self.config.email)
        page.fill('input[name="password"], input[type="password"]', self.config.password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

    def fetch_fundamentals(self, ticker: str) -> dict[str, float]:
        """Scrape fundamental data for a ticker from Koyfin.

        Returns dict of features: pe_ratio, ev_ebitda, fcf_yield, roe, etc.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return {}

        try:
            self._ensure_browser()
            page = self._context.new_page()

            try:
                self._login(page)
                page.goto(
                    f"https://app.koyfin.com/fa/fa/{symbol}",
                    timeout=self.config.timeout_ms,
                )
                page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

                # Extract data from the page
                features: dict[str, float] = {}

                # Try to extract key metrics from the fundamental analysis page
                # Koyfin renders React components, so we need to wait for data
                page.wait_for_timeout(3000)

                # Extract visible metric values
                metrics = page.query_selector_all('[class*="metric"], [class*="value"], [data-testid*="metric"]')
                for metric in metrics:
                    text = metric.inner_text().strip()
                    # Parse numeric values
                    try:
                        if "P/E" in text or "PE" in text:
                            val = self._extract_number(text)
                            if val is not None:
                                features["pe_ratio"] = val
                        elif "EV/EBITDA" in text:
                            val = self._extract_number(text)
                            if val is not None:
                                features["ev_ebitda"] = val
                        elif "ROE" in text:
                            val = self._extract_number(text)
                            if val is not None:
                                features["roe"] = val
                    except Exception:
                        continue

                return features
            finally:
                page.close()

        except Exception as exc:
            logger.warning("Koyfin scrape failed for %s: %s", symbol, exc)
            return {}

    def _extract_number(self, text: str) -> Optional[float]:
        """Extract first number from a text string."""
        import re
        match = re.search(r'[-+]?\d*\.?\d+', text.replace(",", ""))
        if match:
            return float(match.group())
        return None

    def store_fundamentals(
        self,
        ticker: str,
        feature_store: FeatureStore,
        as_of: Optional[str] = None,
    ) -> Optional[str]:
        """Scrape and store Koyfin fundamentals in FeatureStore."""
        features = self.fetch_fundamentals(ticker)
        if not features:
            return None

        event_ts = as_of or datetime.now(timezone.utc).isoformat()
        record = FeatureRecord(
            entity_id=ticker.upper(),
            event_ts=event_ts,
            feature_set="koyfin_fundamentals",
            feature_version=1,
            features=features,
            metadata={"source": self.config.source},
        )

        try:
            feature_store.save(record)
            return record.record_id
        except Exception as exc:
            logger.warning("Failed to store Koyfin data for %s: %s", ticker, exc)
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
