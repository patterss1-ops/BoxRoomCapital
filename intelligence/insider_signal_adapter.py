"""L2 Insider Buying signal adapter (E-002).

Consumes Insider Alpha Radar output (SEC Form 4 insider purchase data)
and produces LayerScore payloads for the Signal Engine composite scorer.

The adapter accepts normalized insider purchase records and computes a
0-100 insider buying score based on four sub-factors:

  1. Cluster Score  (0-40): number of distinct insiders buying within window
  2. Seniority Score (0-25): highest-ranking buyer in the cluster
  3. Conviction Score (0-20): largest single purchase size
  4. Recency Score   (0-15): days since most recent purchase

A net-sell veto is emitted when aggregate selling exceeds buying in the
evaluation window.

The adapter is deliberately decoupled from the Insider Alpha Radar's
internal format.  Callers (cron jobs, webhook handlers, manual import)
create ``InsiderTransaction`` records from whatever source they prefer,
then call ``score_insider_activity()`` to produce a ``LayerScore``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


# ── Input contract ───────────────────────────────────────────────────

class InsiderRole(str, Enum):
    """Canonical insider seniority tiers (SEC Form 4 reporting persons)."""

    CEO = "ceo"
    CHAIRMAN = "chairman"
    CFO = "cfo"
    COO = "coo"
    PRESIDENT = "president"
    VP = "vp"
    DIRECTOR = "director"
    OFFICER = "officer"          # other named officers
    TEN_PCT_OWNER = "10pct_owner"
    OTHER = "other"


class TransactionType(str, Enum):
    """Purchase or sale (derivative exercises excluded for MVP)."""

    PURCHASE = "purchase"
    SALE = "sale"


# Seniority points (higher = more informative signal)
_SENIORITY_POINTS: Dict[InsiderRole, float] = {
    InsiderRole.CEO: 25.0,
    InsiderRole.CHAIRMAN: 25.0,
    InsiderRole.CFO: 20.0,
    InsiderRole.COO: 20.0,
    InsiderRole.PRESIDENT: 20.0,
    InsiderRole.VP: 15.0,
    InsiderRole.DIRECTOR: 15.0,
    InsiderRole.OFFICER: 10.0,
    InsiderRole.TEN_PCT_OWNER: 12.0,
    InsiderRole.OTHER: 5.0,
}


@dataclass(frozen=True)
class InsiderTransaction:
    """One SEC Form 4 insider transaction record.

    This is the adapter's input contract — callers build these from
    whatever data source they have (Insider Alpha Radar JSON, SEC EDGAR
    scraper, third-party API, manual CSV import).
    """

    ticker: str
    insider_name: str
    role: InsiderRole
    transaction_type: TransactionType
    shares: float
    price_per_share: float
    filing_date: str            # ISO-8601 date or datetime
    source_ref: str = ""        # e.g. SEC accession number, Radar event ID
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def value(self) -> float:
        """Total dollar/pound value of the transaction."""
        return abs(self.shares * self.price_per_share)

    @property
    def filing_datetime(self) -> datetime:
        """Parse filing_date to a tz-aware datetime (date-only strings get 00:00 UTC)."""
        text = self.filing_date.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            # Try date-only format
            dt = datetime.strptime(self.filing_date[:10], "%Y-%m-%d")
        # Ensure tz-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


# ── Scoring configuration ────────────────────────────────────────────

@dataclass(frozen=True)
class InsiderScoringConfig:
    """Tunable parameters for the insider scoring model."""

    # Evaluation window in calendar days
    window_days: int = 90

    # Cluster scoring breakpoints: (min_insiders, points)
    cluster_breakpoints: Tuple[Tuple[int, float], ...] = (
        (5, 40.0),
        (3, 30.0),
        (2, 20.0),
        (1, 10.0),
    )

    # Conviction breakpoints: (min_value_usd, points)
    conviction_breakpoints: Tuple[Tuple[float, float], ...] = (
        (1_000_000, 20.0),
        (500_000, 16.0),
        (200_000, 12.0),
        (100_000, 8.0),
        (0, 4.0),
    )

    # Recency breakpoints: (max_days_ago, points)
    recency_breakpoints: Tuple[Tuple[int, float], ...] = (
        (7, 15.0),
        (14, 12.0),
        (30, 9.0),
        (60, 6.0),
        (90, 3.0),
    )

    # Net-sell veto: if aggregate selling > buying, score = 0 + veto
    sell_veto_enabled: bool = True

    # Source label for LayerScore
    source: str = "insider-alpha-radar"


DEFAULT_CONFIG = InsiderScoringConfig()


# ── Scoring engine ───────────────────────────────────────────────────

def _filter_in_window(
    transactions: Sequence[InsiderTransaction],
    as_of: datetime,
    window_days: int,
) -> Tuple[List[InsiderTransaction], List[InsiderTransaction]]:
    """Split transactions into buys/sells within the evaluation window."""
    cutoff = as_of - timedelta(days=window_days)
    buys: List[InsiderTransaction] = []
    sells: List[InsiderTransaction] = []
    for txn in transactions:
        if txn.filing_datetime < cutoff:
            continue
        if txn.filing_datetime > as_of:
            continue
        if txn.transaction_type == TransactionType.PURCHASE:
            buys.append(txn)
        elif txn.transaction_type == TransactionType.SALE:
            sells.append(txn)
    return buys, sells


def _cluster_score(buys: List[InsiderTransaction], config: InsiderScoringConfig) -> float:
    """Score based on number of distinct insiders buying."""
    distinct_insiders = len({txn.insider_name.strip().lower() for txn in buys})
    for min_count, points in config.cluster_breakpoints:
        if distinct_insiders >= min_count:
            return points
    return 0.0


def _seniority_score(buys: List[InsiderTransaction]) -> float:
    """Score based on the highest-ranking insider in the buy cluster."""
    if not buys:
        return 0.0
    return max(_SENIORITY_POINTS.get(txn.role, 0.0) for txn in buys)


def _conviction_score(buys: List[InsiderTransaction], config: InsiderScoringConfig) -> float:
    """Score based on the largest single purchase value."""
    if not buys:
        return 0.0
    max_value = max(txn.value for txn in buys)
    for min_value, points in config.conviction_breakpoints:
        if max_value >= min_value:
            return points
    return 0.0


def _recency_score(
    buys: List[InsiderTransaction],
    as_of: datetime,
    config: InsiderScoringConfig,
) -> float:
    """Score based on how recently the most recent purchase was filed."""
    if not buys:
        return 0.0
    most_recent = max(txn.filing_datetime for txn in buys)
    days_ago = (as_of - most_recent).days
    for max_days, points in config.recency_breakpoints:
        if days_ago <= max_days:
            return points
    return 0.0


def _compute_confidence(
    buys: List[InsiderTransaction],
    as_of: datetime,
    window_days: int,
) -> float:
    """Heuristic confidence in [0, 1] based on data density and recency."""
    if not buys:
        return 0.0
    n_insiders = len({txn.insider_name.strip().lower() for txn in buys})
    most_recent = max(txn.filing_datetime for txn in buys)
    days_ago = (as_of - most_recent).days

    # Density component: more insiders = higher confidence (caps at 5)
    density = min(n_insiders / 5.0, 1.0)
    # Freshness component: more recent = higher confidence
    freshness = max(1.0 - (days_ago / window_days), 0.0)
    return round(0.6 * density + 0.4 * freshness, 4)


def score_insider_activity(
    ticker: str,
    transactions: Sequence[InsiderTransaction],
    as_of: str,
    config: InsiderScoringConfig = DEFAULT_CONFIG,
) -> Tuple[LayerScore, List[str]]:
    """Score insider buying activity for a single ticker.

    Args:
        ticker: The stock symbol to score.
        transactions: All insider transactions for this ticker
            (the function filters by window and direction internally).
        as_of: ISO-8601 evaluation timestamp.
        config: Scoring parameters.

    Returns:
        (LayerScore, vetoes) where vetoes is a list of veto reason strings.
        If there are no purchases in-window, returns score=0 with no vetoes.
    """
    ticker = ticker.strip().upper()
    as_of_text = as_of
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    buys, sells = _filter_in_window(transactions, as_of_dt, config.window_days)

    vetoes: List[str] = []

    # Net-sell veto check
    total_buy_value = sum(txn.value for txn in buys)
    total_sell_value = sum(txn.value for txn in sells)
    net_selling = total_sell_value > total_buy_value

    if config.sell_veto_enabled and net_selling and buys:
        vetoes.append("insider_sell_cluster")

    # Compute sub-scores
    cluster = _cluster_score(buys, config)
    seniority = _seniority_score(buys)
    conviction = _conviction_score(buys, config)
    recency = _recency_score(buys, as_of_dt, config)

    raw_score = cluster + seniority + conviction + recency

    # Apply sell veto: force score to 0
    if vetoes:
        final_score = 0.0
    else:
        final_score = min(max(raw_score, 0.0), 100.0)

    confidence = _compute_confidence(buys, as_of_dt, config.window_days)

    # Build details dict for auditability
    details: Dict[str, Any] = {
        "cluster_count": len({txn.insider_name.strip().lower() for txn in buys}),
        "total_purchases": len(buys),
        "total_sales": len(sells),
        "total_buy_value": round(total_buy_value, 2),
        "total_sell_value": round(total_sell_value, 2),
        "sub_scores": {
            "cluster": cluster,
            "seniority": seniority,
            "conviction": conviction,
            "recency": recency,
        },
        "raw_score": raw_score,
        "vetoed": bool(vetoes),
    }

    # Provenance ref: deterministic from ticker + as_of + buy count
    provenance_ref = f"insider-{ticker}-{as_of_text[:10]}-{len(buys)}buys"

    layer_score = LayerScore(
        layer_id=LayerId.L2_INSIDER,
        ticker=ticker,
        score=final_score,
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )

    return layer_score, vetoes


def score_batch(
    transactions_by_ticker: Dict[str, Sequence[InsiderTransaction]],
    as_of: str,
    config: InsiderScoringConfig = DEFAULT_CONFIG,
) -> Dict[str, Tuple[LayerScore, List[str]]]:
    """Score insider activity for multiple tickers in batch.

    Args:
        transactions_by_ticker: Mapping of ticker -> list of transactions.
        as_of: ISO-8601 evaluation timestamp (same for all tickers).
        config: Scoring parameters.

    Returns:
        Dict mapping ticker -> (LayerScore, vetoes).
    """
    results: Dict[str, Tuple[LayerScore, List[str]]] = {}
    for ticker, txns in transactions_by_ticker.items():
        results[ticker] = score_insider_activity(ticker, txns, as_of, config)
    return results
