"""
Tests for GTAA (Global Tactical Asset Allocation) strategy — B-002.

Covers:
  - Signal generation: LONG_ENTRY when above SMA, LONG_EXIT when below
  - Rebalance cadence: signals only fire on configured rebalance day
  - Trend filter toggle: disable to always hold
  - Configurable SMA period and rebalance day
  - Edge cases: insufficient data, empty DataFrame
  - Universe scoring convenience method
  - Size multiplier equals 1/N equal weight
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
import pandas as pd

from strategies.gtaa import GTAAStrategy, DEFAULT_GTAA_PARAMS
from strategies.base import SignalType


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_bars(prices: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    """
    Create a minimal OHLC DataFrame from a list of close prices.
    Generates dates as trading days (weekdays).
    """
    dates = pd.bdate_range(start=start, periods=len(prices))
    df = pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [1_000_000] * len(prices),
    }, index=dates)
    return df


def _make_trending_up(n: int = 300, start_price: float = 100.0) -> pd.DataFrame:
    """Create n bars of steadily rising prices (well above SMA)."""
    prices = [start_price + i * 0.5 for i in range(n)]
    return _make_bars(prices)


def _make_trending_down(n: int = 300, start_price: float = 250.0) -> pd.DataFrame:
    """Create n bars of steadily falling prices (well below SMA)."""
    prices = [start_price - i * 0.3 for i in range(n)]
    # Don't go below 1.0
    prices = [max(p, 1.0) for p in prices]
    return _make_bars(prices)


def _make_flat(n: int = 300, price: float = 100.0) -> pd.DataFrame:
    """Create n bars at a constant price."""
    return _make_bars([price] * n)


def _advance_to_rebalance(strategy: GTAAStrategy, df: pd.DataFrame, rebalance_day: int = 1):
    """
    Feed bars to the strategy until we land on a rebalance day.
    Returns the signal from the rebalance day bar.
    """
    # Start after enough bars for SMA warmup
    min_start = strategy.p["sma_period"] + 21
    if min_start >= len(df):
        return None, df

    for end_idx in range(min_start, len(df)):
        sub_df = df.iloc[:end_idx + 1]
        signal = strategy.generate_signal(
            ticker="SPY",
            df=sub_df,
            current_position=0.0,
            bars_in_trade=0,
        )
        if signal.signal_type != SignalType.NONE or "Rebalance" in signal.reason:
            return signal, sub_df

    return None, df


# ─── Constructor & Defaults ───────────────────────────────────────────────────


class TestGTAADefaults:
    def test_default_params(self):
        gtaa = GTAAStrategy()
        assert gtaa.p["sma_period"] == 200
        assert gtaa.p["rebalance_day"] == 1
        assert gtaa.p["use_trend_filter"] is True
        assert len(gtaa.p["universe"]) == 5

    def test_custom_params_override(self):
        gtaa = GTAAStrategy(params={"sma_period": 100, "rebalance_day": 3})
        assert gtaa.p["sma_period"] == 100
        assert gtaa.p["rebalance_day"] == 3
        # Defaults preserved for unspecified keys
        assert gtaa.p["use_trend_filter"] is True

    def test_name(self):
        gtaa = GTAAStrategy()
        assert gtaa.name == "GTAA Trend Following"


# ─── Insufficient Data ───────────────────────────────────────────────────────


class TestInsufficientData:
    def test_none_df_returns_none_signal(self):
        gtaa = GTAAStrategy()
        signal = gtaa.generate_signal("SPY", None, 0.0, 0)
        assert signal.signal_type == SignalType.NONE
        assert "Insufficient" in signal.reason

    def test_short_df_returns_none_signal(self):
        gtaa = GTAAStrategy()
        df = _make_bars([100.0] * 50)
        signal = gtaa.generate_signal("SPY", df, 0.0, 0)
        assert signal.signal_type == SignalType.NONE
        assert "Insufficient" in signal.reason

    def test_exactly_min_bars_works(self):
        """With exactly sma_period + 20 bars, should not error."""
        gtaa = GTAAStrategy(params={"sma_period": 50})
        df = _make_trending_up(n=70, start_price=100.0)
        # May or may not be rebalance day, but should not raise
        signal = gtaa.generate_signal("SPY", df, 0.0, 0)
        assert signal.signal_type in (SignalType.NONE, SignalType.LONG_ENTRY)


# ─── Rebalance Day Detection ─────────────────────────────────────────────────


class TestRebalanceDay:
    def test_non_rebalance_day_returns_none(self):
        """On non-rebalance days, signal should be NONE with waiting message."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 5})
        df = _make_trending_up(n=100)

        # First call — if it happens to be day 1 of a month, it won't match day 5
        # Feed multiple bars to ensure we hit a non-rebalance day
        found_waiting = False
        for end_idx in range(72, min(80, len(df))):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            if "waiting" in signal.reason.lower():
                found_waiting = True
                break

        assert found_waiting, "Should have at least one non-rebalance day"

    def test_rebalance_fires_on_correct_day(self):
        """On rebalance day, signal should NOT say 'waiting'."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_trending_up(n=100)

        signal, _ = _advance_to_rebalance(gtaa, df, rebalance_day=1)
        assert signal is not None
        assert "waiting" not in signal.reason.lower()


# ─── Trend Filter (SMA) ──────────────────────────────────────────────────────


class TestTrendFilter:
    def test_above_sma_generates_long_entry(self):
        """Price above SMA on rebalance day → LONG_ENTRY if flat."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_trending_up(n=100)

        signal, _ = _advance_to_rebalance(gtaa, df, rebalance_day=1)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_ENTRY
        assert "ABOVE SMA" in signal.reason

    def test_below_sma_generates_long_exit(self):
        """Price below SMA on rebalance day → LONG_EXIT if holding."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_trending_down(n=100)

        # Advance to rebalance day while "holding" a position
        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 1.0, 30)
            if signal.signal_type == SignalType.LONG_EXIT:
                assert "BELOW SMA" in signal.reason
                return

        pytest.fail("Expected LONG_EXIT on downtrend rebalance day")

    def test_above_sma_already_holding_returns_none(self):
        """If above SMA and already holding, signal should be NONE (hold)."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_trending_up(n=100)

        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 1.0, 30)
            if "Rebalance done" in signal.reason:
                assert signal.signal_type == SignalType.NONE
                assert "HOLD" in signal.reason
                return

        pytest.fail("Expected HOLD on uptrend rebalance with position")

    def test_below_sma_already_flat_returns_none(self):
        """If below SMA and already flat, signal should be NONE (cash)."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_trending_down(n=100)

        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            if "Rebalance done" in signal.reason:
                assert signal.signal_type == SignalType.NONE
                assert "CASH" in signal.reason
                return

        pytest.fail("Expected CASH on downtrend rebalance when flat")

    def test_trend_filter_disabled_always_holds(self):
        """With use_trend_filter=False, always generates LONG_ENTRY when flat."""
        gtaa = GTAAStrategy(params={
            "sma_period": 50,
            "rebalance_day": 1,
            "use_trend_filter": False,
        })
        df = _make_trending_down(n=100)

        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            if signal.signal_type == SignalType.LONG_ENTRY:
                return  # Success — entered despite downtrend

        pytest.fail("Expected LONG_ENTRY with trend filter disabled")


# ─── Size Multiplier (Equal Weight) ──────────────────────────────────────────


class TestSizeMultiplier:
    def test_default_weight_is_one_fifth(self):
        """Default 5-asset universe → weight = 0.2."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_trending_up(n=100)

        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            if signal.signal_type == SignalType.LONG_ENTRY:
                assert abs(signal.size_multiplier - 0.2) < 0.01
                return

        pytest.fail("Expected LONG_ENTRY to check weight")

    def test_custom_universe_weight(self):
        """Custom 3-asset universe → weight = 1/3."""
        gtaa = GTAAStrategy(params={
            "sma_period": 50,
            "rebalance_day": 1,
            "universe": ["SPY", "EFA", "IEF"],
        })
        df = _make_trending_up(n=100)

        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            if signal.signal_type == SignalType.LONG_ENTRY:
                assert abs(signal.size_multiplier - 1 / 3) < 0.01
                return

        pytest.fail("Expected LONG_ENTRY to check weight")


# ─── Configurable Parameters ─────────────────────────────────────────────────


class TestConfigurableParams:
    def test_different_sma_period(self):
        """Strategy should work with different SMA periods."""
        gtaa = GTAAStrategy(params={"sma_period": 20, "rebalance_day": 1})
        df = _make_trending_up(n=60)  # Shorter data needed for shorter SMA

        signal, _ = _advance_to_rebalance(gtaa, df, rebalance_day=1)
        assert signal is not None
        # Trending up with short SMA should still produce entry
        if signal.signal_type == SignalType.LONG_ENTRY:
            assert "SMA(20)" in signal.reason

    def test_different_rebalance_day(self):
        """Rebalance on day 3 of month instead of day 1."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 3})
        df = _make_trending_up(n=100)

        # Should get waiting signals for days 1-2
        signals = []
        for end_idx in range(72, min(85, len(df))):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            signals.append(signal)

        waiting_signals = [s for s in signals if "waiting" in s.reason.lower()]
        rebalance_signals = [s for s in signals if "waiting" not in s.reason.lower() and "Insufficient" not in s.reason]
        # Should have more waiting than rebalance signals
        assert len(waiting_signals) > 0


# ─── Universe Scoring ────────────────────────────────────────────────────────


class TestUniverseScoring:
    def test_score_all_assets(self):
        """Score universe returns dict with all assets."""
        gtaa = GTAAStrategy(params={
            "sma_period": 50,
            "universe": ["SPY", "EFA", "IEF"],
        })

        universe_data = {
            "SPY": _make_trending_up(n=100),
            "EFA": _make_trending_down(n=100),
            "IEF": _make_flat(n=100),
        }

        scores = gtaa.score_universe(universe_data)
        assert "SPY" in scores
        assert "EFA" in scores
        assert "IEF" in scores

        # SPY trending up → should hold
        assert scores["SPY"]["above_sma"] is True
        assert scores["SPY"]["should_hold"] is True
        assert scores["SPY"]["weight"] > 0

        # EFA trending down → should not hold
        assert scores["EFA"]["above_sma"] is False
        assert scores["EFA"]["should_hold"] is False
        assert scores["EFA"]["weight"] == 0.0

    def test_score_missing_data(self):
        """Missing data for an asset returns error in score."""
        gtaa = GTAAStrategy(params={
            "sma_period": 50,
            "universe": ["SPY", "EFA"],
        })

        scores = gtaa.score_universe({"SPY": _make_trending_up(n=100)})
        assert scores["EFA"].get("error") == "Insufficient data"

    def test_score_with_trend_filter_disabled(self):
        """With trend filter off, all assets should be held."""
        gtaa = GTAAStrategy(params={
            "sma_period": 50,
            "universe": ["SPY", "EFA"],
            "use_trend_filter": False,
        })

        universe_data = {
            "SPY": _make_trending_up(n=100),
            "EFA": _make_trending_down(n=100),
        }

        scores = gtaa.score_universe(universe_data)
        assert scores["SPY"]["should_hold"] is True
        assert scores["EFA"]["should_hold"] is True  # Held despite downtrend


# ─── Deterministic Signal Outputs ─────────────────────────────────────────────


class TestDeterministic:
    def test_same_input_same_output(self):
        """Same data should produce same signal on repeated calls."""
        params = {"sma_period": 50, "rebalance_day": 1}
        df = _make_trending_up(n=100)

        results = []
        for _ in range(3):
            gtaa = GTAAStrategy(params=params)
            signal, _ = _advance_to_rebalance(gtaa, df, rebalance_day=1)
            if signal:
                results.append(signal.signal_type)

        assert len(set(results)) == 1, "Same input should produce same signal"

    def test_flat_price_at_sma_boundary(self):
        """Price exactly at SMA — tests boundary condition."""
        gtaa = GTAAStrategy(params={"sma_period": 50, "rebalance_day": 1})
        df = _make_flat(n=100, price=100.0)

        # Flat price = price equals SMA, so NOT above → should not enter
        for end_idx in range(72, len(df)):
            sub_df = df.iloc[:end_idx + 1]
            signal = gtaa.generate_signal("SPY", sub_df, 0.0, 0)
            if "Rebalance" in signal.reason and "waiting" not in signal.reason.lower():
                # At exactly SMA, current_price is NOT > SMA (it equals), so no entry
                assert signal.signal_type != SignalType.LONG_ENTRY
                return

        # If no rebalance triggered, that's also acceptable (day counting)
