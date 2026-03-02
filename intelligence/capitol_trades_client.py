"""Capitol Trades data client (F-003).

Normalizes congressional trading disclosure data for use by the
L5 Congressional Trading signal layer.

Members of Congress are required to disclose stock trades within
45 days of execution.  Academic research (Eggers & Hainmueller 2013,
Ziobrowski et al. 2004) shows that congressional portfolios
outperform the market by 5-12% annually, suggesting information
asymmetry.

This client accepts pre-fetched records from Capitol Trades,
Quiver Quantitative, or manual import and normalizes them into
``CongressionalTrade`` records for downstream scoring.

The client is data-source agnostic — callers provide raw data
from whichever source they prefer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class TradeDirection(str, Enum):
    """Congressional trade direction."""

    BUY = "buy"
    SELL = "sell"


class Chamber(str, Enum):
    """Congressional chamber."""

    SENATE = "senate"
    HOUSE = "house"


@dataclass(frozen=True)
class CongressionalTrade:
    """One congressional trading disclosure record.

    Callers build these from Capitol Trades, Quiver Quantitative,
    or manual entry.
    """

    ticker: str
    member_name: str
    chamber: Chamber
    direction: TradeDirection
    trade_date: str           # ISO-8601 date of the trade
    disclosure_date: str      # ISO-8601 date of disclosure filing
    estimated_value_low: float    # lower bound of reported range
    estimated_value_high: float   # upper bound of reported range
    committee_memberships: tuple[str, ...] = ()  # relevant committees
    source_ref: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def midpoint_value(self) -> float:
        """Estimated midpoint of the trade value range."""
        return (self.estimated_value_low + self.estimated_value_high) / 2.0

    @property
    def filing_lag_days(self) -> int:
        """Days between trade execution and public disclosure."""
        try:
            trade_dt = datetime.fromisoformat(self.trade_date.replace("Z", "+00:00"))
            disc_dt = datetime.fromisoformat(self.disclosure_date.replace("Z", "+00:00"))
            return max((disc_dt - trade_dt).days, 0)
        except (ValueError, TypeError):
            return 0

    @property
    def is_committee_relevant(self) -> bool:
        """Whether the member sits on a potentially relevant committee.

        Committees with outsized information advantage:
          - Senate Banking, Finance, Armed Services, Intelligence, Commerce
          - House Financial Services, Ways and Means, Armed Services,
            Intelligence, Energy and Commerce
        """
        relevant_keywords = {
            "banking", "finance", "financial", "armed services",
            "intelligence", "commerce", "energy", "ways and means",
            "health", "appropriations",
        }
        for committee in self.committee_memberships:
            lower = committee.lower()
            if any(kw in lower for kw in relevant_keywords):
                return True
        return False


def normalize_trades(
    raw_records: Sequence[Dict[str, Any]],
    ticker: str,
) -> List[CongressionalTrade]:
    """Normalize raw Capitol Trades-style records into CongressionalTrade list.

    Each record should have keys: member_name, chamber, direction,
    trade_date, disclosure_date, estimated_value_low, estimated_value_high.
    Optional: committee_memberships, source_ref.
    """
    results: List[CongressionalTrade] = []
    for record in raw_records:
        try:
            trade = CongressionalTrade(
                ticker=ticker.strip().upper(),
                member_name=str(record["member_name"]),
                chamber=Chamber(str(record["chamber"]).lower()),
                direction=TradeDirection(str(record["direction"]).lower()),
                trade_date=str(record["trade_date"]),
                disclosure_date=str(record["disclosure_date"]),
                estimated_value_low=float(record["estimated_value_low"]),
                estimated_value_high=float(record["estimated_value_high"]),
                committee_memberships=tuple(
                    str(c) for c in record.get("committee_memberships", ())
                ),
                source_ref=str(record.get("source_ref", "")),
                metadata=dict(record.get("metadata", {})),
            )
            results.append(trade)
        except (KeyError, ValueError, TypeError):
            continue
    return results
