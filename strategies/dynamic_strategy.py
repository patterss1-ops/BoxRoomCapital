"""
Dynamic Strategy — interprets a JSON strategy spec at runtime.

Used by the Idea Pipeline to turn LLM-generated strategy specifications
into executable trading strategies, without writing custom code per idea.

Safety: purely declarative rule evaluation — NO eval/exec.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType
from data.provider import (
    calc_ibs,
    calc_rsi,
    calc_ema,
    calc_sma,
    calc_atr,
    calc_adx,
    calc_donchian_upper,
    calc_donchian_lower,
    calc_consecutive_down_days,
)

logger = logging.getLogger(__name__)

# ── Indicator registry ───────────────────────────────────────────────────────

INDICATOR_REGISTRY: dict[str, Callable] = {
    "ibs": lambda df, **kw: calc_ibs(df),
    "rsi": lambda df, **kw: calc_rsi(df["Close"], kw.get("period", 14)),
    "ema": lambda df, **kw: calc_ema(df["Close"], kw.get("period", 20)),
    "sma": lambda df, **kw: calc_sma(df["Close"], kw.get("period", 20)),
    "atr": lambda df, **kw: calc_atr(df, kw.get("period", 14)),
    "adx": lambda df, **kw: calc_adx(df, kw.get("period", 14)),
    "donchian_upper": lambda df, **kw: calc_donchian_upper(df["High"], kw.get("period", 20)),
    "donchian_lower": lambda df, **kw: calc_donchian_lower(df["Low"], kw.get("period", 20)),
    "consecutive_down_days": lambda df, **kw: calc_consecutive_down_days(df["Close"]),
    "close": lambda df, **kw: df["Close"],
    "open": lambda df, **kw: df["Open"],
    "high": lambda df, **kw: df["High"],
    "low": lambda df, **kw: df["Low"],
    "volume": lambda df, **kw: df["Volume"],
}

VALID_OPERATORS = {"<", ">", "<=", ">=", "crosses_above", "crosses_below"}


# ── Spec validation ──────────────────────────────────────────────────────────

def validate_strategy_spec(spec: dict) -> list[str]:
    """Validate a strategy spec. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    if not isinstance(spec, dict):
        return ["Spec must be a dict"]

    if not spec.get("name"):
        errors.append("Missing 'name'")

    direction = spec.get("direction", "")
    if direction not in ("long", "short"):
        errors.append(f"Invalid direction '{direction}' — must be 'long' or 'short'")

    entry_rules = spec.get("entry_rules", [])
    if not entry_rules:
        errors.append("Must have at least 1 entry rule")

    exit_rules = spec.get("exit_rules", [])
    if not exit_rules:
        errors.append("Must have at least 1 exit rule")

    for i, rule in enumerate(entry_rules):
        errors.extend(_validate_rule(rule, f"entry_rules[{i}]"))

    for i, rule in enumerate(exit_rules):
        errors.extend(_validate_rule(rule, f"exit_rules[{i}]"))

    # VIX filter
    vix = spec.get("vix_filter", {})
    if vix and vix.get("enabled"):
        ml = vix.get("max_level")
        if ml is not None and (not isinstance(ml, (int, float)) or ml <= 0):
            errors.append("vix_filter.max_level must be a positive number")

    # Stop loss
    sl = spec.get("stop_loss_atr_multiple")
    if sl is not None and (not isinstance(sl, (int, float)) or sl <= 0):
        errors.append("stop_loss_atr_multiple must be a positive number")

    return errors


def _validate_rule(rule: dict, prefix: str) -> list[str]:
    """Validate a single entry/exit rule."""
    errors: list[str] = []

    if not isinstance(rule, dict):
        return [f"{prefix}: rule must be a dict"]

    # max_hold is a special exit-only rule
    if rule.get("type") == "max_hold":
        bars = rule.get("bars")
        if not isinstance(bars, (int, float)) or bars < 1:
            errors.append(f"{prefix}: max_hold bars must be >= 1")
        return errors

    indicator = rule.get("indicator", "")
    if indicator not in INDICATOR_REGISTRY:
        errors.append(f"{prefix}: unknown indicator '{indicator}'")

    period = rule.get("period")
    if period is not None:
        if not isinstance(period, (int, float)) or period < 2 or period > 500:
            errors.append(f"{prefix}: period must be 2-500")

    operator = rule.get("operator", "")
    if operator not in VALID_OPERATORS:
        errors.append(f"{prefix}: invalid operator '{operator}'")

    # Must have value OR reference (not both required, but at least one)
    has_value = "value" in rule
    has_ref = "reference" in rule
    if not has_value and not has_ref:
        errors.append(f"{prefix}: must have 'value' or 'reference'")

    if has_ref:
        ref = rule["reference"]
        if ref not in INDICATOR_REGISTRY:
            errors.append(f"{prefix}: unknown reference indicator '{ref}'")
        ref_period = rule.get("ref_period")
        if ref_period is not None and (not isinstance(ref_period, (int, float)) or ref_period < 2 or ref_period > 500):
            errors.append(f"{prefix}: ref_period must be 2-500")

    return errors


# ── Rule evaluation helpers ──────────────────────────────────────────────────

def _compute_indicator(df: pd.DataFrame, name: str, period: int | None = None) -> pd.Series:
    """Compute a named indicator on the dataframe."""
    fn = INDICATOR_REGISTRY[name]
    kwargs = {}
    if period is not None:
        kwargs["period"] = int(period)
    return fn(df, **kwargs)


def _evaluate_operator(lhs: pd.Series, op: str, rhs: pd.Series) -> pd.Series:
    """Evaluate a comparison operator on two series, returning boolean series."""
    if op == "<":
        return lhs < rhs
    elif op == ">":
        return lhs > rhs
    elif op == "<=":
        return lhs <= rhs
    elif op == ">=":
        return lhs >= rhs
    elif op == "crosses_above":
        return (lhs > rhs) & (lhs.shift(1) <= rhs.shift(1))
    elif op == "crosses_below":
        return (lhs < rhs) & (lhs.shift(1) >= rhs.shift(1))
    else:
        return pd.Series(False, index=lhs.index)


def _evaluate_rule(df: pd.DataFrame, rule: dict) -> pd.Series:
    """Evaluate a single rule, returning a boolean series."""
    if rule.get("type") == "max_hold":
        # max_hold is checked separately by bars_in_trade
        return pd.Series(False, index=df.index)

    indicator = rule["indicator"]
    period = rule.get("period")
    lhs = _compute_indicator(df, indicator, period)

    op = rule["operator"]

    if "reference" in rule:
        ref_period = rule.get("ref_period")
        rhs = _compute_indicator(df, rule["reference"], ref_period)
    else:
        rhs = pd.Series(rule["value"], index=df.index, dtype=float)

    return _evaluate_operator(lhs, op, rhs)


# ── DynamicStrategy ──────────────────────────────────────────────────────────

class DynamicStrategy(BaseStrategy):
    """Executes a JSON strategy specification at runtime."""

    def __init__(self, spec: dict):
        errors = validate_strategy_spec(spec)
        if errors:
            raise ValueError(f"Invalid strategy spec: {'; '.join(errors)}")
        self.spec = spec

    @property
    def name(self) -> str:
        return f"Dynamic: {self.spec.get('name', 'Unnamed')}"

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        vix_close: Optional[float] = None,
        **kwargs,
    ) -> Signal:
        if len(df) < 210:
            return Signal(SignalType.NONE, ticker, self.name, "Insufficient data")

        direction = self.spec["direction"]
        entry_rules = self.spec.get("entry_rules", [])
        exit_rules = self.spec.get("exit_rules", [])

        # VIX filter
        vix_filter = self.spec.get("vix_filter", {})
        if vix_filter.get("enabled") and vix_close is not None:
            max_vix = vix_filter.get("max_level", 35)
            if vix_close > max_vix:
                return Signal(SignalType.NONE, ticker, self.name,
                              f"VIX {vix_close:.1f} > max {max_vix}")

        is_long = direction == "long"

        # ── Exit logic (ANY rule triggers) ──
        if (is_long and current_position > 0) or (not is_long and current_position < 0):
            # Check max_hold
            for rule in exit_rules:
                if rule.get("type") == "max_hold":
                    if bars_in_trade >= rule.get("bars", 999):
                        exit_type = SignalType.LONG_EXIT if is_long else SignalType.SHORT_EXIT
                        return Signal(exit_type, ticker, self.name,
                                      f"Max hold {rule['bars']} bars reached")

            # Check indicator-based exit rules (OR logic)
            for rule in exit_rules:
                if rule.get("type") == "max_hold":
                    continue
                try:
                    result = _evaluate_rule(df, rule)
                    if result.iloc[-1]:
                        exit_type = SignalType.LONG_EXIT if is_long else SignalType.SHORT_EXIT
                        return Signal(exit_type, ticker, self.name,
                                      f"Exit rule triggered: {rule.get('indicator', '')} {rule.get('operator', '')} {rule.get('value', rule.get('reference', ''))}")
                except Exception as exc:
                    logger.warning("Exit rule eval error: %s", exc)

            # Check stop loss
            sl_mult = self.spec.get("stop_loss_atr_multiple")
            if sl_mult and bars_in_trade > 0:
                try:
                    atr = calc_atr(df, 14)
                    atr_val = atr.iloc[-1]
                    entry_price = df["Close"].iloc[-bars_in_trade] if bars_in_trade < len(df) else df["Close"].iloc[0]
                    current_price = df["Close"].iloc[-1]
                    if is_long:
                        stop = entry_price - sl_mult * atr_val
                        if current_price < stop:
                            return Signal(SignalType.LONG_EXIT, ticker, self.name,
                                          f"Stop loss hit: {current_price:.2f} < {stop:.2f}")
                    else:
                        stop = entry_price + sl_mult * atr_val
                        if current_price > stop:
                            return Signal(SignalType.SHORT_EXIT, ticker, self.name,
                                          f"Stop loss hit: {current_price:.2f} > {stop:.2f}")
                except Exception as exc:
                    logger.warning("Stop loss check error: %s", exc)

            return Signal(SignalType.NONE, ticker, self.name, "Holding")

        # ── Entry logic (ALL rules must pass) ──
        if current_position == 0:
            all_pass = True
            for rule in entry_rules:
                try:
                    result = _evaluate_rule(df, rule)
                    if not result.iloc[-1]:
                        all_pass = False
                        break
                except Exception as exc:
                    logger.warning("Entry rule eval error: %s", exc)
                    all_pass = False
                    break

            if all_pass:
                entry_type = SignalType.LONG_ENTRY if is_long else SignalType.SHORT_ENTRY
                return Signal(entry_type, ticker, self.name,
                              f"All {len(entry_rules)} entry rules passed")

        return Signal(SignalType.NONE, ticker, self.name, "No signal")
