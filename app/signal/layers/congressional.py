"""L5 Congressional Trading scorer (F-003).

Computes a 0-100 score based on congressional trading activity
for a given ticker.

Academic evidence (Eggers & Hainmueller 2013, Ziobrowski et al. 2004)
shows that:

  - Congressional portfolios outperform the market by 5-12% annually.
  - Buys by members on relevant oversight committees are the
    strongest signal (information asymmetry).
  - Cluster buys (multiple members buying the same stock) amplify
    the signal.
  - Filing lag < 30 days correlates with higher signal quality
    (fresher information).

Score composition:
  1. Net Direction Score (0-30): buy/sell ratio weighted by value
  2. Cluster Score      (0-25): multiple members trading same direction
  3. Committee Score    (0-25): committee relevance of the members
  4. Recency Score      (0-20): filing lag and trade recency

The scorer is data-source agnostic.  Callers provide
``CongressionalTrade`` records from Capitol Trades, Quiver
Quantitative, or manual import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from intelligence.capitol_trades_client import (
    Chamber,
    CongressionalTrade,
    TradeDirection,
)


# ── Scoring configuration ────────────────────────────────────────────

@dataclass(frozen=True)
class CongressionalScoringConfig:
    """Tunable parameters for the congressional trading scoring model."""

    # Evaluation window in calendar days
    window_days: int = 90

    # Net direction scoring: buy_value_ratio → points
    # buy_value_ratio = buy_value / (buy_value + sell_value), range [0, 1]
    direction_buy_breakpoints: Tuple[Tuple[float, float], ...] = (
        (0.9, 30.0),    # near-unanimous buying
        (0.8, 25.0),
        (0.7, 20.0),
        (0.6, 15.0),
        (0.5, 10.0),    # balanced
    )

    # Sell-heavy direction breakpoints: (max_ratio, points)
    direction_sell_breakpoints: Tuple[Tuple[float, float], ...] = (
        (0.1, 0.0),     # near-unanimous selling
        (0.2, 3.0),
        (0.3, 5.0),
        (0.4, 7.0),
    )

    # Cluster scoring: distinct members trading in net direction → points
    cluster_breakpoints: Tuple[Tuple[int, float], ...] = (
        (5, 25.0),      # 5+ distinct members
        (4, 22.0),
        (3, 18.0),
        (2, 12.0),
        (1, 5.0),
    )

    # Committee relevance scoring
    committee_all_relevant_score: float = 25.0
    committee_some_relevant_score: float = 18.0
    committee_one_relevant_score: float = 10.0
    committee_none_relevant_score: float = 3.0

    # Recency scoring: based on average filing lag
    recency_breakpoints: Tuple[Tuple[int, float], ...] = (
        (15, 20.0),     # avg lag <= 15 days — freshest
        (30, 16.0),
        (45, 12.0),     # within legal deadline
        (60, 8.0),
        (90, 4.0),
    )

    # Source label
    source: str = "capitol-trades"


DEFAULT_CONFIG = CongressionalScoringConfig()


# ── Scoring engine ───────────────────────────────────────────────────

def _filter_in_window(
    trades: Sequence[CongressionalTrade],
    as_of: datetime,
    window_days: int,
) -> List[CongressionalTrade]:
    """Filter trades to those with trade_date within the evaluation window."""
    cutoff = as_of - timedelta(days=window_days)
    results = []
    for t in trades:
        try:
            dt = datetime.fromisoformat(t.trade_date.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if cutoff <= dt <= as_of:
                results.append(t)
        except (ValueError, TypeError):
            continue
    return results


def _direction_score(
    trades: List[CongressionalTrade],
    config: CongressionalScoringConfig,
) -> float:
    """Score based on net value-weighted buy/sell direction."""
    if not trades:
        return 0.0

    buy_value = sum(
        t.midpoint_value for t in trades
        if t.direction == TradeDirection.BUY
    )
    sell_value = sum(
        t.midpoint_value for t in trades
        if t.direction == TradeDirection.SELL
    )
    total = buy_value + sell_value
    if total <= 0:
        return 0.0

    buy_ratio = buy_value / total

    if buy_ratio >= 0.5:
        for min_ratio, points in config.direction_buy_breakpoints:
            if buy_ratio >= min_ratio:
                return points
        return 0.0
    else:
        for max_ratio, points in config.direction_sell_breakpoints:
            if buy_ratio <= max_ratio:
                return points
        return 7.0  # slight sell bias


def _net_direction_trades(trades: List[CongressionalTrade]) -> List[CongressionalTrade]:
    """Return only the trades in the value-weighted net direction.

    If buy_value >= sell_value → return buy trades only.
    Otherwise → return sell trades only.

    This ensures cluster/committee/recency only score trades that
    align with the dominant signal direction.
    """
    buy_value = sum(t.midpoint_value for t in trades
                    if t.direction == TradeDirection.BUY)
    sell_value = sum(t.midpoint_value for t in trades
                     if t.direction == TradeDirection.SELL)

    if buy_value >= sell_value:
        return [t for t in trades if t.direction == TradeDirection.BUY]
    return [t for t in trades if t.direction == TradeDirection.SELL]


def _cluster_score(
    net_trades: List[CongressionalTrade],
    config: CongressionalScoringConfig,
) -> float:
    """Score based on number of distinct members in the net direction.

    Only counts members whose trades align with the value-weighted
    net direction (P1 fix: prevents inflated cluster score when a
    few tiny trades outnumber one large opposing trade).
    """
    if not net_trades:
        return 0.0

    net_members = {t.member_name.strip().lower() for t in net_trades}
    net_count = len(net_members)

    for min_count, points in config.cluster_breakpoints:
        if net_count >= min_count:
            return points
    return 0.0


def _committee_score(
    net_trades: List[CongressionalTrade],
    config: CongressionalScoringConfig,
) -> float:
    """Score based on committee relevance of net-direction members."""
    if not net_trades:
        return config.committee_none_relevant_score

    relevant_count = sum(1 for t in net_trades if t.is_committee_relevant)
    total = len(net_trades)

    if total == 0:
        return config.committee_none_relevant_score

    relevance_ratio = relevant_count / total

    if relevance_ratio >= 0.8:
        return config.committee_all_relevant_score
    elif relevance_ratio >= 0.5:
        return config.committee_some_relevant_score
    elif relevance_ratio > 0:
        return config.committee_one_relevant_score
    return config.committee_none_relevant_score


def _recency_score(
    net_trades: List[CongressionalTrade],
    config: CongressionalScoringConfig,
) -> float:
    """Score based on average filing lag of net-direction trades."""
    if not net_trades:
        return 0.0

    lags = [t.filing_lag_days for t in net_trades]
    avg_lag = sum(lags) / len(lags)

    for max_lag, points in config.recency_breakpoints:
        if avg_lag <= max_lag:
            return points
    return 0.0


def _compute_confidence(
    trades: List[CongressionalTrade],
    window_days: int,
) -> float:
    """Heuristic confidence in [0, 1]."""
    if not trades:
        return 0.0

    # Filing count: more trades = higher confidence
    count_factor = min(len(trades) / 10.0, 1.0)
    # Distinct members: more members = higher conviction
    n_members = len({t.member_name.strip().lower() for t in trades})
    member_factor = min(n_members / 5.0, 1.0)

    return round(0.5 * count_factor + 0.5 * member_factor, 4)


def score_congressional(
    ticker: str,
    trades: Sequence[CongressionalTrade],
    as_of: str,
    config: CongressionalScoringConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Score congressional trading activity for a single ticker.

    Args:
        ticker: Stock symbol.
        trades: CongressionalTrade records for this ticker.
        as_of: ISO-8601 evaluation timestamp.
        config: Scoring parameters.

    Returns:
        LayerScore with layer_id=L5_CONGRESSIONAL.
    """
    ticker = ticker.strip().upper()
    as_of_text = as_of
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    filtered = _filter_in_window(trades, as_of_dt, config.window_days)

    if not filtered:
        return LayerScore(
            layer_id=LayerId.L5_CONGRESSIONAL,
            ticker=ticker,
            score=0.0,
            as_of=as_of_text,
            source=config.source,
            provenance_ref=f"congress-{ticker}-{as_of_text[:10]}-no-data",
            confidence=0.0,
            details={"reason": "no_trades_in_window"},
        )

    direction = _direction_score(filtered, config)
    net_trades = _net_direction_trades(filtered)
    cluster = _cluster_score(net_trades, config)
    committee = _committee_score(net_trades, config)
    recency = _recency_score(net_trades, config)

    raw_score = direction + cluster + committee + recency
    final_score = min(max(raw_score, 0.0), 100.0)

    confidence = _compute_confidence(filtered, config.window_days)

    buy_trades = [t for t in filtered if t.direction == TradeDirection.BUY]
    sell_trades = [t for t in filtered if t.direction == TradeDirection.SELL]
    net_trade_value = (
        sum(t.midpoint_value for t in buy_trades)
        - sum(t.midpoint_value for t in sell_trades)
    )
    avg_lag = sum(t.filing_lag_days for t in filtered) / len(filtered)
    relevant_count = sum(1 for t in filtered if t.is_committee_relevant)

    details: Dict[str, Any] = {
        "filing_count": len(filtered),
        "filing_lag_days": round(avg_lag, 1),
        "committee_relevance": round(relevant_count / len(filtered), 4),
        "net_trade_value": round(net_trade_value, 2),
        "buy_count": len(buy_trades),
        "sell_count": len(sell_trades),
        "distinct_members": len({t.member_name.strip().lower() for t in filtered}),
        "sub_scores": {
            "direction": direction,
            "cluster": cluster,
            "committee": committee,
            "recency": recency,
        },
        "raw_score": raw_score,
    }

    provenance_ref = (
        f"congress-{ticker}-{as_of_text[:10]}"
        f"-{len(buy_trades)}buy-{len(sell_trades)}sell"
        f"-{len({t.member_name.strip().lower() for t in filtered})}members"
    )

    return LayerScore(
        layer_id=LayerId.L5_CONGRESSIONAL,
        ticker=ticker,
        score=round(final_score, 2),
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_congressional_batch(
    trades_by_ticker: Dict[str, Sequence[CongressionalTrade]],
    as_of: str,
    config: CongressionalScoringConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Score congressional trading for multiple tickers.

    Returns dict mapping ticker -> LayerScore.
    """
    return {
        ticker: score_congressional(ticker, tlist, as_of, config)
        for ticker, tlist in trades_by_ticker.items()
    }
