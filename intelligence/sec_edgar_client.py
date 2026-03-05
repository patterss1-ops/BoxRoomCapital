"""SEC EDGAR Form 4 scraper for L2 Insider Buying signals.

Fetches insider transaction filings from the SEC EDGAR full-text search API
(free, no key required, 10 req/s with User-Agent header).
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import requests

from intelligence.insider_signal_adapter import InsiderRole, InsiderTransaction, TransactionType

logger = logging.getLogger(__name__)

_EDGAR_FULL_TEXT_URL = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_FILING_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
_EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_API_URL = "https://data.sec.gov"
_USER_AGENT = "BoxRoomCapital research@boxroomcapital.com"

_ROLE_MAP = {
    "chief executive officer": InsiderRole.CEO,
    "ceo": InsiderRole.CEO,
    "chief financial officer": InsiderRole.CFO,
    "cfo": InsiderRole.CFO,
    "chief operating officer": InsiderRole.COO,
    "coo": InsiderRole.COO,
    "chairman": InsiderRole.CHAIRMAN,
    "president": InsiderRole.PRESIDENT,
    "vice president": InsiderRole.VP,
    "vp": InsiderRole.VP,
    "director": InsiderRole.DIRECTOR,
    "10% owner": InsiderRole.TEN_PCT_OWNER,
    "ten percent owner": InsiderRole.TEN_PCT_OWNER,
}


@dataclass(frozen=True)
class EdgarClientConfig:
    """Configuration for SEC EDGAR client."""

    user_agent: str = _USER_AGENT
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay: float = 0.12  # ~8 req/s to stay under 10/s limit
    source: str = "sec-edgar-form4"


def _parse_role(title: str) -> InsiderRole:
    """Map SEC filing officer title to InsiderRole."""
    lower = title.strip().lower()
    for key, role in _ROLE_MAP.items():
        if key in lower:
            return role
    if "officer" in lower:
        return InsiderRole.OFFICER
    return InsiderRole.OTHER


def _parse_form4_xml(xml_text: str, ticker: str) -> list[InsiderTransaction]:
    """Parse SEC EDGAR Form 4 XML into InsiderTransaction records."""
    transactions: list[InsiderTransaction] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return transactions

    # Extract reporter info
    reporter = root.find(".//reportingOwner")
    if reporter is None:
        return transactions

    name_elem = reporter.find(".//rptOwnerName")
    title_elem = reporter.find(".//officerTitle")
    insider_name = name_elem.text.strip() if name_elem is not None and name_elem.text else "Unknown"
    role = _parse_role(title_elem.text if title_elem is not None and title_elem.text else "")

    # Parse non-derivative transactions
    for txn in root.findall(".//nonDerivativeTransaction"):
        try:
            code_elem = txn.find(".//transactionCoding/transactionCode")
            if code_elem is None or code_elem.text is None:
                continue
            code = code_elem.text.strip().upper()

            # P = Purchase, S = Sale
            if code == "P":
                txn_type = TransactionType.PURCHASE
            elif code == "S":
                txn_type = TransactionType.SALE
            else:
                continue

            shares_elem = txn.find(".//transactionAmounts/transactionShares/value")
            price_elem = txn.find(".//transactionAmounts/transactionPricePerShare/value")
            date_elem = txn.find(".//transactionDate/value")

            if shares_elem is None or shares_elem.text is None:
                continue
            shares = float(shares_elem.text)
            price = float(price_elem.text) if price_elem is not None and price_elem.text else 0.0
            filing_date = date_elem.text.strip() if date_elem is not None and date_elem.text else ""

            transactions.append(
                InsiderTransaction(
                    ticker=ticker.upper(),
                    insider_name=insider_name,
                    role=role,
                    transaction_type=txn_type,
                    shares=shares,
                    price_per_share=price,
                    filing_date=filing_date,
                    source_ref="sec-edgar-form4",
                )
            )
        except (ValueError, TypeError):
            continue

    return transactions


class SECEdgarClient:
    """Client for fetching SEC EDGAR Form 4 filings."""

    def __init__(
        self,
        config: Optional[EdgarClientConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self.config = config or EdgarClientConfig()
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": self.config.user_agent})
        self._sleep = sleep_fn

    def fetch_insider_transactions(
        self,
        ticker: str,
        days_back: int = 90,
    ) -> list[InsiderTransaction]:
        """Fetch Form 4 insider transactions for a ticker from SEC EDGAR.

        Uses the SEC company filings endpoint to find recent Form 4 filings,
        then parses the XML to extract transaction data.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        transactions: list[InsiderTransaction] = []

        try:
            # Use SEC company search to find CIK
            cik_url = f"{_EDGAR_API_URL}/submissions/CIK{symbol}.json"
            # Try ticker-based lookup first
            search_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt={(datetime.now(timezone.utc).date().isoformat())}&forms=4&hits.hits.total=10"

            # Use the full-text search API for recent Form 4 filings
            params = {
                "q": f'"{symbol}"',
                "forms": "4",
                "dateRange": "custom",
                "startdt": datetime.now(timezone.utc).date().isoformat(),
            }

            # Simpler approach: use EDGAR full-text search
            url = "https://efts.sec.gov/LATEST/search-index"
            resp = self._session.get(
                f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=4",
                timeout=self.config.timeout_seconds,
            )

            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                for hit in hits[:10]:  # Limit to 10 most recent
                    self._sleep(self.config.request_delay)
                    filing_url = hit.get("_source", {}).get("file_url", "")
                    if not filing_url:
                        continue
                    try:
                        xml_resp = self._session.get(
                            f"https://www.sec.gov{filing_url}",
                            timeout=self.config.timeout_seconds,
                        )
                        if xml_resp.status_code == 200:
                            txns = _parse_form4_xml(xml_resp.text, symbol)
                            transactions.extend(txns)
                    except Exception:
                        continue

        except Exception as exc:
            logger.warning("SEC EDGAR fetch failed for %s: %s", symbol, exc)

        return transactions

    def fetch_batch(
        self,
        tickers: Sequence[str],
        days_back: int = 90,
    ) -> dict[str, list[InsiderTransaction]]:
        """Fetch insider transactions for multiple tickers."""
        result: dict[str, list[InsiderTransaction]] = {}
        for ticker in tickers:
            self._sleep(self.config.request_delay)
            result[ticker.upper()] = self.fetch_insider_transactions(ticker, days_back)
        return result
