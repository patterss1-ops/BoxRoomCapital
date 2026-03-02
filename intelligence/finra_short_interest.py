"""FINRA short-interest data client (F-002).

Fetches and normalizes FINRA short-interest settlement data for use by
the L3 Short Interest Dynamics signal layer.

FINRA publishes short-interest data twice per month (settlement dates
around the 15th and end of month).  This client accepts pre-fetched
records and normalizes them into ``ShortInterestSnapshot`` records for
downstream scoring.

The client is data-source agnostic — callers provide raw data from
FINRA APIs, broker feeds, or manual CSV import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class ShortInterestSnapshot:
    """One FINRA short-interest settlement snapshot for a ticker.

    Callers build these from FINRA data feeds, broker short-interest
    reports, or third-party APIs.
    """

    ticker: str
    settlement_date: str          # ISO-8601 date of FINRA settlement
    short_interest: int           # absolute shares short
    avg_daily_volume: float       # avg daily volume for days-to-cover calc
    shares_outstanding: int       # total shares outstanding
    prior_short_interest: Optional[int] = None  # previous settlement period
    source_ref: str = ""          # provenance reference
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def short_interest_pct(self) -> float:
        """Short interest as % of shares outstanding."""
        if self.shares_outstanding <= 0:
            return 0.0
        return (self.short_interest / self.shares_outstanding) * 100.0

    @property
    def days_to_cover(self) -> float:
        """Days to cover = short interest / avg daily volume."""
        if self.avg_daily_volume <= 0:
            return 0.0
        return self.short_interest / self.avg_daily_volume

    @property
    def short_interest_change_pct(self) -> Optional[float]:
        """% change in short interest from prior settlement.

        Positive = shorts increasing (bearish).
        Negative = shorts decreasing (potential short squeeze / bullish).
        """
        if self.prior_short_interest is None or self.prior_short_interest <= 0:
            return None
        return ((self.short_interest - self.prior_short_interest)
                / self.prior_short_interest) * 100.0


def normalize_snapshots(
    raw_records: Sequence[Dict[str, Any]],
    ticker: str,
) -> List[ShortInterestSnapshot]:
    """Normalize raw FINRA-style records into ShortInterestSnapshot list.

    Each record should have keys: settlement_date, short_interest,
    avg_daily_volume, shares_outstanding, and optionally
    prior_short_interest and source_ref.
    """
    results: List[ShortInterestSnapshot] = []
    for record in raw_records:
        try:
            snap = ShortInterestSnapshot(
                ticker=ticker.strip().upper(),
                settlement_date=str(record["settlement_date"]),
                short_interest=int(record["short_interest"]),
                avg_daily_volume=float(record.get("avg_daily_volume", 0)),
                shares_outstanding=int(record.get("shares_outstanding", 0)),
                prior_short_interest=(
                    int(record["prior_short_interest"])
                    if record.get("prior_short_interest") is not None
                    else None
                ),
                source_ref=str(record.get("source_ref", "")),
                metadata=dict(record.get("metadata", {})),
            )
            results.append(snap)
        except (KeyError, ValueError, TypeError):
            continue
    return results
