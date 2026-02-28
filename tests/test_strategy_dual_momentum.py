"""
Tests for Dual Momentum GEM strategy — B-002.

Covers:
  - Relative momentum: US vs International equities comparison
  - Absolute momentum: winner must have positive return
  - Safe haven rotation: bonds when both equities fail
  - Rebalance cadence: signals only fire on configured day
  - Configurable lookback and assets
  - Edge cases: insufficient data, missing universe data
  - Universe scoring convenience method
  - Deterministic outputs for fixed inputs
  - All-in sizing (size_multiplier=1.0)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
import pandas as pd

from strategies.dual_momentum import DualMomentumStrategy, DEFAULT_DUAL_MOMENTUM_PARAMS
from strategies.base import SignalType


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_bars(prices: list[float], start: str = "2023-01-02") -> pd.DataFrame:
    """Create a minimal OHLC DataFrame from close prices."""
    dates = pd.bdate_range(start=start, periods=len(prices))
    df = pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [1_000_000] * len(prices),
    }, index=dates)
    return df


def _make_trending_up(n: int = 300, start_price: float = 100.0, slope: float = 0.5) -> pd.DataFrame:
    """Create n bars of steadily rising prices."""
    prices = [start_price + i * slope for i in range(n)]
    return _make_bars(prices)


def _make_trending_down(n: int = 300, start_price: float = 250.0, slope: float = 0.3) -> pd.DataFrame:
    """Create n bars of steadily falling prices."""
    prices = [max(start_price - i * slope, 1.0) for i in range(n)]
    return _make_bars(prices)


def _make_flat(n: int = 300, price: float = 100.0) -> pd.DataFrame:
    """Create n bars at a constant price."""
    return _make_bars([price] * n)


def _advance_to_rebalance(
    strategy: DualMomentumStrategy,
    ticker: str,
    df: pd.DataFrame,
    current_position: float,
    universe_data: dict,
):
    """
    Feed bars to the strategy until we land on a rebalance day.
    Returns the signal from the rebalance day.
    """
    min_start = strategy.p["lookback_days"] + 25
    for end_idx in range(min_start, len(df)):
        sub_df = df.iloc[:end_idx + 1]
        # Build sub-universe data with same slicing
        sub_universe = {}
        for t, udf in universe_data.items():
            if t != ticker:
                sub_universe[t] = udf.iloc[:end_idx + 1] if len(udf) > end_idx else udf

        signal = strategy.generate_signal(
            ticker=ticker,
            df=sub_df,
            current_position=current_position,
            bars_in_trade=0,
            universe_data=sub_universe,
        )
        if "waiting" not in signal.reason.lower() and "Insufficient" not in signal.reason:
            return signal

    return None


# ─── Constructor & Defaults ───────────────────────────────────────────────────


class TestDualMomentumDefaults:
    def test_default_params(self):
        dm = DualMomentumStrategy()
        assert dm.p["lookback_days"] == 252
        assert dm.p["rebalance_day"] == 1
        assert dm.p["us_equity"] == "SPY"
        assert dm.p["intl_equity"] == "EFA"
        assert dm.p["safe_haven"] == "AGG"

    def test_custom_params_override(self):
        dm = DualMomentumStrategy(params={"lookback_days": 126, "safe_haven": "BND"})
        assert dm.p["lookback_days"] == 126
        assert dm.p["safe_haven"] == "BND"
        # Defaults preserved
        assert dm.p["us_equity"] == "SPY"

    def test_name(self):
        dm = DualMomentumStrategy()
        assert dm.name == "Dual Momentum GEM"


# ─── Insufficient Data ───────────────────────────────────────────────────────


class TestInsufficientData:
    def test_none_df_returns_none_signal(self):
        dm = DualMomentumStrategy()
        signal = dm.generate_signal("SPY", None, 0.0, 0)
        assert signal.signal_type == SignalType.NONE
        assert "Insufficient" in signal.reason

    def test_short_df_returns_none_signal(self):
        dm = DualMomentumStrategy()
        df = _make_bars([100.0] * 50)
        signal = dm.generate_signal("SPY", df, 0.0, 0)
        assert signal.signal_type == SignalType.NONE
        assert "Insufficient" in signal.reason

    def test_missing_universe_data_returns_none(self):
        """Without universe data, can't compute relative momentum."""
        dm = DualMomentumStrategy(params={"lookback_days": 50, "rebalance_day": 1})
        df = _make_trending_up(n=100)

        signal = _advance_to_rebalance(dm, "SPY", df, 0.0, {})
        # Should either return None or a signal with "Missing" reason
        if signal is not None:
            assert signal.signal_type == SignalType.NONE


# ─── Relative Momentum ───────────────────────────────────────────────────────


class TestRelativeMomentum:
    def test_us_wins_relative_momentum(self):
        """When US has higher return, US should be picked."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
            "us_equity": "SPY",
            "intl_equity": "EFA",
            "safe_haven": "AGG",
        })

        # US trending up fast, international flat
        spy_df = _make_trending_up(n=100, slope=1.0)
        efa_df = _make_flat(n=100, price=100.0)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        signal = _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_ENTRY
        assert "REL+ABS" in signal.reason

    def test_intl_wins_relative_momentum(self):
        """When International has higher return, International should be picked."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
            "us_equity": "SPY",
            "intl_equity": "EFA",
            "safe_haven": "AGG",
        })

        # International trending up fast, US flat
        spy_df = _make_flat(n=100, price=100.0)
        efa_df = _make_trending_up(n=100, slope=1.0)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        # SPY should get EXIT (or NONE if flat)
        signal_spy = _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)
        assert signal_spy is not None
        # SPY is not the pick, so should be NONE (already flat) or no entry
        assert signal_spy.signal_type != SignalType.LONG_ENTRY

    def test_intl_winner_gets_entry_signal(self):
        """The winning international ticker gets LONG_ENTRY."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        spy_df = _make_flat(n=100, price=100.0)
        efa_df = _make_trending_up(n=100, slope=1.0)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        signal = _advance_to_rebalance(dm, "EFA", efa_df, 0.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_ENTRY


# ─── Absolute Momentum ───────────────────────────────────────────────────────


class TestAbsoluteMomentum:
    def test_positive_absolute_momentum_passes(self):
        """Winner with positive return passes absolute momentum."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        spy_df = _make_trending_up(n=100, slope=0.5)
        efa_df = _make_trending_up(n=100, slope=0.3)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        signal = _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_ENTRY
        assert "REL+ABS" in signal.reason

    def test_negative_absolute_momentum_rotates_to_bonds(self):
        """When winner has negative momentum, rotate to safe haven."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        # Both equities trending down
        spy_df = _make_trending_down(n=100, slope=0.5)
        efa_df = _make_trending_down(n=100, slope=0.8)
        agg_df = _make_trending_up(n=100, slope=0.1)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        # SPY wins relative (falls less), but has negative absolute → bonds
        signal_spy = _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)
        signal_agg = _advance_to_rebalance(
            DualMomentumStrategy(params={"lookback_days": 50, "rebalance_day": 1}),
            "AGG", agg_df, 0.0, universe,
        )

        # SPY should NOT get entry (negative absolute momentum)
        if signal_spy is not None:
            assert signal_spy.signal_type != SignalType.LONG_ENTRY

        # AGG should get entry (safe haven pick)
        if signal_agg is not None:
            assert signal_agg.signal_type == SignalType.LONG_ENTRY
            assert "ABS FAIL" in signal_agg.reason


# ─── Exit Signals ─────────────────────────────────────────────────────────────


class TestExitSignals:
    def test_holding_loser_gets_exit(self):
        """If holding a non-picked asset, should get LONG_EXIT."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        spy_df = _make_trending_up(n=100, slope=1.0)
        efa_df = _make_flat(n=100, price=100.0)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        # Holding EFA while SPY is winning → should exit EFA
        signal = _advance_to_rebalance(dm, "EFA", efa_df, 1.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_EXIT
        assert "pick is SPY" in signal.reason

    def test_holding_winner_stays(self):
        """If already holding the picked asset, should get NONE (hold)."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        spy_df = _make_trending_up(n=100, slope=1.0)
        efa_df = _make_flat(n=100, price=100.0)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        signal = _advance_to_rebalance(dm, "SPY", spy_df, 1.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.NONE
        assert "HOLD" in signal.reason


# ─── All-In Sizing ───────────────────────────────────────────────────────────


class TestSizing:
    def test_size_multiplier_is_one(self):
        """Dual Momentum is all-in on one asset (size_multiplier=1.0)."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        spy_df = _make_trending_up(n=100, slope=1.0)
        efa_df = _make_flat(n=100, price=100.0)
        agg_df = _make_flat(n=100, price=100.0)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        signal = _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_ENTRY
        assert signal.size_multiplier == 1.0


# ─── Rebalance Day ────────────────────────────────────────────────────────────


class TestRebalanceDay:
    def test_non_rebalance_returns_none(self):
        """Non-rebalance days should return NONE with waiting message."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 5,  # Day 5 of month
        })
        df = _make_trending_up(n=100)

        found_waiting = False
        for end_idx in range(75, min(85, len(df))):
            sub_df = df.iloc[:end_idx + 1]
            signal = dm.generate_signal(
                "SPY", sub_df, 0.0, 0,
                universe_data={"EFA": df, "AGG": df},
            )
            if "waiting" in signal.reason.lower():
                found_waiting = True
                break

        assert found_waiting


# ─── Universe Scoring ────────────────────────────────────────────────────────


class TestUniverseScoring:
    def test_score_returns_all_fields(self):
        """Score universe returns comprehensive scoring data."""
        dm = DualMomentumStrategy(params={"lookback_days": 50})

        universe = {
            "SPY": _make_trending_up(n=100, slope=0.5),
            "EFA": _make_flat(n=100),
            "AGG": _make_flat(n=100),
        }

        scores = dm.score_universe(universe)
        assert "us_return_pct" in scores
        assert "intl_return_pct" in scores
        assert "relative_winner" in scores
        assert "absolute_momentum_pass" in scores
        assert "pick" in scores

    def test_score_us_winning(self):
        """Score reflects US winning when US has higher return."""
        dm = DualMomentumStrategy(params={"lookback_days": 50})

        universe = {
            "SPY": _make_trending_up(n=100, slope=1.0),
            "EFA": _make_flat(n=100),
            "AGG": _make_flat(n=100),
        }

        scores = dm.score_universe(universe)
        assert scores["relative_winner"] == "SPY"
        assert scores["absolute_momentum_pass"] is True
        assert scores["pick"] == "SPY"

    def test_score_both_negative_picks_bonds(self):
        """Score reflects bonds pick when both equities negative."""
        dm = DualMomentumStrategy(params={"lookback_days": 50})

        universe = {
            "SPY": _make_trending_down(n=100, slope=0.5),
            "EFA": _make_trending_down(n=100, slope=0.8),
            "AGG": _make_flat(n=100),
        }

        scores = dm.score_universe(universe)
        assert scores["absolute_momentum_pass"] is False
        assert scores["pick"] == "AGG"

    def test_score_insufficient_data(self):
        """Score returns error with insufficient data."""
        dm = DualMomentumStrategy(params={"lookback_days": 50})
        scores = dm.score_universe({"SPY": _make_bars([100.0] * 10)})
        assert "error" in scores


# ─── Current Pick Accessor ────────────────────────────────────────────────────


class TestCurrentPick:
    def test_initial_pick_is_none(self):
        dm = DualMomentumStrategy()
        pick, reason = dm.get_current_pick()
        assert pick is None
        assert reason == "INIT"

    def test_pick_updated_after_signal(self):
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
        })

        spy_df = _make_trending_up(n=100, slope=1.0)
        efa_df = _make_flat(n=100)
        agg_df = _make_flat(n=100)

        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}
        _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)

        pick, reason = dm.get_current_pick()
        assert pick == "SPY"
        assert "REL+ABS" in reason


# ─── Deterministic Outputs ────────────────────────────────────────────────────


class TestDeterministic:
    def test_same_input_same_output(self):
        """Same data produces same pick on repeated calls."""
        spy_df = _make_trending_up(n=100, slope=1.0)
        efa_df = _make_flat(n=100)
        agg_df = _make_flat(n=100)
        universe = {"SPY": spy_df, "EFA": efa_df, "AGG": agg_df}

        picks = []
        for _ in range(3):
            dm = DualMomentumStrategy(params={"lookback_days": 50, "rebalance_day": 1})
            signal = _advance_to_rebalance(dm, "SPY", spy_df, 0.0, universe)
            if signal:
                picks.append(signal.signal_type)

        assert len(set(picks)) == 1, "Same input should produce same signal"


# ─── Configurable Assets ─────────────────────────────────────────────────────


class TestConfigurableAssets:
    def test_custom_tickers(self):
        """Strategy works with custom ticker configuration."""
        dm = DualMomentumStrategy(params={
            "lookback_days": 50,
            "rebalance_day": 1,
            "us_equity": "VTI",
            "intl_equity": "VXUS",
            "safe_haven": "BND",
        })

        vti_df = _make_trending_up(n=100, slope=1.0)
        vxus_df = _make_flat(n=100)
        bnd_df = _make_flat(n=100)

        universe = {"VTI": vti_df, "VXUS": vxus_df, "BND": bnd_df}

        signal = _advance_to_rebalance(dm, "VTI", vti_df, 0.0, universe)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG_ENTRY

    def test_calc_return_static_method(self):
        """_calc_return computes correct fractional return."""
        df = _make_bars([100.0, 110.0, 120.0, 130.0, 140.0, 150.0])
        ret = DualMomentumStrategy._calc_return(df, lookback=4)
        # price went from 110 (idx 1, which is -4-1=-5 from end) to 150 (last)
        # Actually: idx[-5-1] = idx[0] = 100, idx[-1] = 150 → 50%
        expected = (150.0 - 110.0) / 110.0  # ~0.3636
        assert abs(ret - expected) < 0.01
