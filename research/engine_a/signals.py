"""Deterministic Engine A signal generators."""

from __future__ import annotations

import math
from collections.abc import Sequence


def _as_list(values: Sequence[float]) -> list[float]:
    return [float(value) for value in values]


def _ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    estimate = values[0]
    for value in values[1:]:
        estimate = alpha * value + (1 - alpha) * estimate
    return estimate


def _sigmoid_normalize(value: float, scale: float = 1.0) -> float:
    scaled = value / scale if scale else value
    return max(-1.0, min(1.0, 2.0 / (1.0 + math.exp(-scaled)) - 1.0))


def _percentile_normalize(value: float, history: list[float]) -> float:
    if not history:
        return 0.0
    less_or_equal = sum(1 for item in history if item <= value)
    percentile = less_or_equal / len(history)
    return round(max(-1.0, min(1.0, percentile * 2.0 - 1.0)), 6)


class TrendSignal:
    """EWMA crossover blended across multiple lookbacks."""

    LOOKBACKS = [8, 16, 32, 64]

    def compute(self, prices: Sequence[float]) -> float:
        values = _as_list(prices)
        if len(values) < max(self.LOOKBACKS) + 1:
            return 0.0
        atr_proxy = max(1e-6, sum(abs(values[i] - values[i - 1]) for i in range(-20, 0)) / 20.0)
        pairs = [(8, 16), (16, 32), (32, 64)]
        raw = mean((_ema(values, fast) - _ema(values, slow)) / atr_proxy for fast, slow in pairs)
        return round(_sigmoid_normalize(raw, scale=1.5), 6)


class CarrySignal:
    """Annualized carry from term structure."""

    def compute(
        self,
        front_price: float,
        deferred_price: float,
        days_to_roll: int,
        history: Sequence[float] | None = None,
    ) -> float:
        if front_price <= 0 or deferred_price <= 0 or days_to_roll <= 0:
            return 0.0
        carry = (float(front_price) - float(deferred_price)) / float(front_price) * (365.0 / days_to_roll)
        hist = _as_list(history or [])
        if hist:
            return _percentile_normalize(carry, hist + [carry])
        return round(_sigmoid_normalize(carry, scale=0.15), 6)


class ValueSignal:
    """Z-score of real yield or price-to-fair-value."""

    def compute(self, current_value: float, history: Sequence[float], lookback: int = 1260) -> float:
        values = _as_list(history)[-lookback:]
        if len(values) < 20:
            return 0.0
        avg = mean(values)
        variance = sum((value - avg) ** 2 for value in values) / len(values)
        stdev = math.sqrt(variance)
        if stdev == 0:
            return 0.0
        z_score = max(-3.0, min(3.0, (float(current_value) - avg) / stdev))
        return round(z_score / 3.0, 6)


class MomentumSignal:
    """12-month return minus last month, normalized by historical percentile."""

    LOOKBACK_12M = 252
    LOOKBACK_1M = 21

    def compute(self, prices: Sequence[float]) -> float:
        values = _as_list(prices)
        if len(values) < self.LOOKBACK_12M + self.LOOKBACK_1M:
            return 0.0
        latest = self._momentum(values, len(values) - 1)
        history = [
            self._momentum(values, idx)
            for idx in range(self.LOOKBACK_12M + self.LOOKBACK_1M, len(values))
        ]
        scale = _absolute_percentile(history, 0.9)
        if scale == 0:
            return 0.0
        return round(max(-1.0, min(1.0, latest / scale)), 6)

    def _momentum(self, prices: list[float], end_idx: int) -> float:
        current = prices[end_idx]
        one_month_ago = prices[end_idx - self.LOOKBACK_1M]
        twelve_months_ago = prices[end_idx - self.LOOKBACK_12M]
        ret_12m = (one_month_ago / twelve_months_ago) - 1.0
        ret_1m = (current / one_month_ago) - 1.0
        return ret_12m - ret_1m


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _absolute_percentile(values: list[float], percentile: float) -> float:
    absolute = sorted(abs(value) for value in values)
    if not absolute:
        return 0.0
    index = min(len(absolute) - 1, max(0, int(round((len(absolute) - 1) * percentile))))
    return absolute[index]
