"""Runtime data assembly for Engine A from the research market-data layer."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import config
from data.pg_connection import get_pg_connection, release_pg_connection
from research.market_data.futures import (
    build_continuous_series,
    build_multiple_prices,
    get_carry_series,
    get_front_contract,
)
from research.market_data.instruments import get_instrument
from research.shared.sql import fetchall_dicts


def _parse_as_of(as_of: str) -> date:
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def _returns(prices: list[float]) -> list[float]:
    if len(prices) < 2:
        return []
    results: list[float] = []
    for prev, current in zip(prices[:-1], prices[1:]):
        if prev == 0:
            continue
        results.append((current / prev) - 1.0)
    return results


def _correlation(left: list[float], right: list[float]) -> float:
    count = min(len(left), len(right))
    if count < 2:
        return 0.0
    x = left[-count:]
    y = right[-count:]
    mean_x = _mean(x)
    mean_y = _mean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denom_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
    denom_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
    if denom_x == 0 or denom_y == 0:
        return 0.0
    return max(-0.99, min(0.99, numerator / (denom_x * denom_y)))


def _rolling_price_to_mean(prices: list[float], window: int = 126) -> list[float]:
    if not prices:
        return []
    series: list[float] = []
    for idx in range(len(prices)):
        start = max(0, idx - window + 1)
        window_prices = prices[start : idx + 1]
        avg = _mean(window_prices) or prices[idx]
        series.append(prices[idx] / avg)
    return series


def _drawdown_pct(prices: list[float]) -> float:
    peak = None
    worst = 0.0
    for price in prices:
        peak = price if peak is None else max(peak, price)
        if peak:
            worst = min(worst, (price / peak) - 1.0)
    return worst * 100.0


def _percentile_rank(values: list[float], current: float) -> float:
    if not values:
        return 50.0
    below = sum(1 for value in values if value <= current)
    return round(100.0 * below / len(values), 2)


_ENGINE_A_EXECUTION_PROFILES: dict[str, dict[str, Any]] = {
    "ES": {"contract_multiplier": 5.0, "instrument_type": "micro_equity", "broker": "ibkr", "asset_class": "index"},
    "NQ": {"contract_multiplier": 2.0, "instrument_type": "micro_equity", "broker": "ibkr", "asset_class": "index"},
    "YM": {"contract_multiplier": 0.5, "instrument_type": "micro_equity", "broker": "ibkr", "asset_class": "index"},
    "RTY": {"contract_multiplier": 5.0, "instrument_type": "micro_equity", "broker": "ibkr", "asset_class": "index"},
    "ZN": {"contract_multiplier": 1000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "rates"},
    "ZF": {"contract_multiplier": 1000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "rates"},
    "ZB": {"contract_multiplier": 1000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "rates"},
    "GC": {"contract_multiplier": 10.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "SI": {"contract_multiplier": 1000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "CL": {"contract_multiplier": 100.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "NG": {"contract_multiplier": 1000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "HG": {"contract_multiplier": 2500.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "6E": {"contract_multiplier": 125000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "fx"},
    "6B": {"contract_multiplier": 62500.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "fx"},
    "6J": {"contract_multiplier": 12500000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "fx"},
    "ZC": {"contract_multiplier": 5000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "ZS": {"contract_multiplier": 5000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
    "ZW": {"contract_multiplier": 5000.0, "instrument_type": "standard", "broker": "ibkr", "asset_class": "commodity"},
}


def _execution_profile(root_symbol: str, multiplier: float) -> dict[str, Any]:
    profile = dict(_ENGINE_A_EXECUTION_PROFILES.get(root_symbol, {}))
    profile.setdefault("contract_multiplier", float(multiplier or 1.0))
    profile.setdefault("instrument_type", "standard")
    profile.setdefault("broker", "ibkr")
    profile.setdefault("asset_class", "index")
    return profile


class EngineARuntimeDataProvider:
    """Assemble the numeric payload expected by EngineAPipeline from PostgreSQL."""

    def __init__(
        self,
        capital_base: float = config.ENGINE_A_CAPITAL_BASE,
        lookback_bars: int = 320,
        min_history: int = 180,
        carry_lookback_days: int = 90,
        connection_factory=get_pg_connection,
        release_factory=release_pg_connection,
    ):
        self._capital_base = float(capital_base)
        self._lookback_bars = int(lookback_bars)
        self._min_history = int(min_history)
        self._carry_lookback_days = max(1, int(carry_lookback_days))
        self._get_connection = connection_factory
        self._release_connection = release_factory

    def __call__(self, as_of: str) -> dict[str, Any]:
        as_of_date = _parse_as_of(as_of)
        root_symbols = self._root_symbols()
        if not root_symbols:
            raise ValueError("No futures roots found in research.futures_contracts")

        price_history: dict[str, list[float]] = {}
        term_structure: dict[str, dict[str, Any]] = {}
        value_history: dict[str, list[float]] = {}
        current_value: dict[str, float] = {}
        vol_estimates: dict[str, float] = {}
        contract_sizes: dict[str, float] = {}
        current_positions: dict[str, float] = {}
        instrument_profiles: dict[str, dict[str, Any]] = {}

        for root_symbol in root_symbols:
            front_contract = get_front_contract(root_symbol, as_of_date)
            multiple = build_multiple_prices(root_symbol, as_of_date)
            if front_contract is None or multiple is None:
                continue

            continuous = [
                item.price
                for item in build_continuous_series(root_symbol)
                if item.bar_date <= as_of_date and item.price is not None
            ]
            if len(continuous) < self._min_history:
                continue
            prices = continuous[-self._lookback_bars :]
            returns = _returns(prices)
            if len(returns) < 30:
                continue

            instrument = get_instrument(front_contract.instrument_id)
            multiplier = float(instrument.multiplier) if instrument and instrument.multiplier is not None else 1.0
            profile = _execution_profile(root_symbol, multiplier)
            current_price = float(multiple.current_price)
            next_price = float(multiple.next_price) if multiple.next_price is not None else current_price
            carry_points = get_carry_series(
                root_symbol,
                start=max(as_of_date - timedelta(days=self._carry_lookback_days), date.min),
                end=as_of_date,
            )

            price_history[root_symbol] = [float(price) for price in prices]
            value_series = _rolling_price_to_mean(prices, window=126)
            value_history[root_symbol] = value_series
            current_value[root_symbol] = float(value_series[-1])
            term_structure[root_symbol] = {
                "front_price": current_price,
                "deferred_price": next_price,
                "days_to_roll": max(1, (front_contract.expiry_date - as_of_date).days),
                "carry_history": [float(point["carry"]) for point in carry_points[-90:]],
            }
            vol_estimates[root_symbol] = max(0.05, round(_std(returns) * math.sqrt(252), 6))
            contract_sizes[root_symbol] = max(1.0, abs(current_price * float(profile["contract_multiplier"])))
            current_positions[root_symbol] = 0.0
            instrument_profiles[root_symbol] = {
                "instrument_type": str(profile["instrument_type"]),
                "broker": str(profile["broker"]),
                "asset_class": str(profile["asset_class"]),
                "contract_multiplier": float(profile["contract_multiplier"]),
            }

        if not price_history:
            raise ValueError("Insufficient canonical futures history to build Engine A inputs")

        correlations = self._build_correlations(price_history)
        regime_inputs = self._build_regime_inputs(price_history, vol_estimates, term_structure)
        return {
            "regime_inputs": regime_inputs,
            "price_history": price_history,
            "term_structure": term_structure,
            "value_history": value_history,
            "current_value": current_value,
            "vol_estimates": vol_estimates,
            "correlations": correlations,
            "current_positions": current_positions,
            "instrument_profiles": instrument_profiles,
            "capital": self._capital_base,
            "contract_sizes": contract_sizes,
            "instrument_type": "future",
            "broker": "ibkr",
            "asset_class": "futures",
            "data_version": as_of_date.isoformat(),
        }

    def _root_symbols(self) -> list[str]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT root_symbol
                    FROM research.futures_contracts
                    ORDER BY root_symbol ASC
                    """,
                )
                rows = fetchall_dicts(cur)
            return [str(row["root_symbol"]) for row in rows]
        finally:
            self._release_connection(conn)

    @staticmethod
    def _build_correlations(price_history: dict[str, list[float]]) -> dict[str, dict[str, float]]:
        returns = {instrument: _returns(prices) for instrument, prices in price_history.items()}
        matrix: dict[str, dict[str, float]] = {}
        for left, left_returns in returns.items():
            matrix[left] = {}
            for right, right_returns in returns.items():
                matrix[left][right] = 1.0 if left == right else round(_correlation(left_returns, right_returns), 6)
        return matrix

    @staticmethod
    def _build_regime_inputs(
        price_history: dict[str, list[float]],
        vol_estimates: dict[str, float],
        term_structure: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        instruments = sorted(price_history)
        trend_scores: list[float] = []
        breadth_hits = 0
        reversal_hits = 0
        drawdowns: list[float] = []

        for instrument in instruments:
            prices = price_history[instrument]
            latest = prices[-1]
            fast_mean = _mean(prices[-63:])
            slow_mean = _mean(prices[-252:]) if len(prices) >= 252 else _mean(prices)
            trend_scores.append(((latest / fast_mean) - 1.0) + ((fast_mean / slow_mean) - 1.0))
            if latest >= _mean(prices[-126:]):
                breadth_hits += 1
            if len(prices) >= 22:
                short_term = (prices[-1] / prices[-6]) - 1.0
                medium_term = (prices[-1] / prices[-22]) - 1.0
                if short_term * medium_term < 0:
                    reversal_hits += 1
            drawdowns.append(_drawdown_pct(prices))

        avg_vol = _mean(list(vol_estimates.values()))
        carry_bps = []
        for payload in term_structure.values():
            front = float(payload["front_price"])
            deferred = float(payload["deferred_price"])
            if front:
                carry_bps.append(((deferred - front) / front) * 10000.0)

        return {
            "vix": round(avg_vol * 100.0, 2),
            "vix_percentile": _percentile_rank(list(vol_estimates.values()), avg_vol),
            "index_data": {
                "trend_score": round(_mean(trend_scores), 6),
                "breadth": round(breadth_hits / max(1, len(instruments)), 6),
                "reversal_probability": round(reversal_hits / max(1, len(instruments)), 6),
            },
            "yield_data": {
                "spread_bps": round(_mean(carry_bps), 2),
            },
            "macro_data": {
                "credit_spread_bps": round(100.0 + avg_vol * 250.0, 2),
                "equity_drawdown_pct": round(_mean(drawdowns), 2),
            },
        }
