"""ShareScope web scraper for UK quality screens and director dealings.

Uses Playwright headless browser to log in and extract data from ShareScope's
web version. Requires SHARESCOPE_EMAIL and SHARESCOPE_PASSWORD env vars.
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
class ShareScopeConfig:
    """Configuration for ShareScope scraper."""

    email: str = ""
    password: str = ""
    timeout_ms: int = 30000
    source: str = "sharescope-scraper"


class ShareScopeScraper:
    """Playwright-based scraper for ShareScope UK stock data."""

    def __init__(self, config: Optional[ShareScopeConfig] = None):
        cfg = config or ShareScopeConfig()
        self.config = ShareScopeConfig(
            email=cfg.email or os.getenv("SHARESCOPE_EMAIL", ""),
            password=cfg.password or os.getenv("SHARESCOPE_PASSWORD", ""),
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

    def _login(self, page):
        """Log into ShareScope web."""
        if not self.config.email or not self.config.password:
            raise ValueError("SHARESCOPE_EMAIL and SHARESCOPE_PASSWORD required")

        page.goto("https://www.sharescope.co.uk/login", timeout=self.config.timeout_ms)
        page.fill('input[name="email"], input[type="email"]', self.config.email)
        page.fill('input[name="password"], input[type="password"]', self.config.password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)

    def fetch_uk_screen(self, screen_name: str = "quality") -> list[dict[str, Any]]:
        """Run a pre-configured ShareScope screen and extract results.

        Args:
            screen_name: Name of screen to run (quality, income, momentum)

        Returns:
            List of dicts with ticker, name, and screen-specific fields.
        """
        results: list[dict[str, Any]] = []

        try:
            self._ensure_browser()
            page = self._context.new_page()

            try:
                self._login(page)
                # Navigate to screens section
                page.goto(
                    f"https://www.sharescope.co.uk/screens/{screen_name}",
                    timeout=self.config.timeout_ms,
                )
                page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
                page.wait_for_timeout(3000)

                # Extract table rows
                rows = page.query_selector_all('table tbody tr, [class*="row"]')
                for row in rows[:50]:  # Limit
                    cells = row.query_selector_all("td, [class*='cell']")
                    if len(cells) >= 2:
                        ticker = cells[0].inner_text().strip()
                        name = cells[1].inner_text().strip()
                        if ticker:
                            results.append({
                                "ticker": ticker,
                                "name": name,
                                "screen": screen_name,
                            })
            finally:
                page.close()

        except Exception as exc:
            logger.warning("ShareScope screen '%s' failed: %s", screen_name, exc)

        return results

    def store_uk_screen(
        self,
        screen_name: str,
        feature_store: FeatureStore,
        as_of: Optional[str] = None,
    ) -> int:
        """Run screen and store results in FeatureStore."""
        results = self.fetch_uk_screen(screen_name)
        stored = 0

        event_ts = as_of or datetime.now(timezone.utc).isoformat()
        for item in results:
            ticker = item.get("ticker", "")
            if not ticker:
                continue

            record = FeatureRecord(
                entity_id=ticker,
                event_ts=event_ts,
                feature_set="sharescope_uk_screen",
                feature_version=1,
                features={"screen_hit": 1.0},
                metadata={"source": self.config.source, "screen": screen_name, "name": item.get("name", "")},
            )
            try:
                feature_store.save(record)
                stored += 1
            except Exception:
                continue

        return stored

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
