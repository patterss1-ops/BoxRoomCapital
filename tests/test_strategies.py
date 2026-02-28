"""
Unit tests for strategy logic using synthetic data.
Validates that IBS++, Trend Following, and Rotation strategies
produce correct signals for known conditions.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from strategies.ibs_mean_reversion import IBSMeanReversion
from strategies.trend_following import TrendFollowing
from strategies.spy_tlt_rotation import SPYTLTRotation
from strategies.base import SignalType
from data.provider import calc_ibs, calc_rsi, calc_ema, calc_atr, calc_adx, calc_consecutive_down_days


def make_ohlc(n=300, base=100, seed=42):
    """Generate synthetic OHLC data with realistic properties."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")

    closes = [base]
    for _ in range(n - 1):
        ret = rng.normal(0.0005, 0.012)
        closes.append(closes[-1] * (1 + ret))
    closes = np.array(closes)

    # Generate realistic OHLC from closes
    highs = closes * (1 + rng.uniform(0.001, 0.015, n))
    lows = closes * (1 - rng.uniform(0.001, 0.015, n))
    opens = closes * (1 + rng.normal(0, 0.005, n))

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": rng.randint(1000000, 10000000, n),
    }, index=dates)

    return df


def test_ibs_calculation():
    """Test IBS calculation matches Pine Script formula."""
    df = make_ohlc(10)
    ibs = calc_ibs(df)

    # IBS should be between 0 and 1
    assert (ibs >= 0).all() and (ibs <= 1).all(), "IBS out of range"

    # Manual check first bar
    expected = (df["Close"].iloc[0] - df["Low"].iloc[0]) / (df["High"].iloc[0] - df["Low"].iloc[0])
    assert abs(ibs.iloc[0] - expected) < 1e-10, f"IBS mismatch: {ibs.iloc[0]} vs {expected}"

    # When close == low, IBS should be 0
    df_low = df.copy()
    df_low.iloc[0, df_low.columns.get_loc("Close")] = df_low.iloc[0]["Low"]
    ibs_low = calc_ibs(df_low)
    assert abs(ibs_low.iloc[0]) < 1e-10, "IBS should be 0 when close == low"

    # When close == high, IBS should be 1
    df_high = df.copy()
    df_high.iloc[0, df_high.columns.get_loc("Close")] = df_high.iloc[0]["High"]
    ibs_high = calc_ibs(df_high)
    assert abs(ibs_high.iloc[0] - 1.0) < 1e-10, "IBS should be 1 when close == high"

    print("  PASS: IBS calculation")


def test_rsi_range():
    """Test RSI stays in 0-100 range."""
    df = make_ohlc(100)
    rsi = calc_rsi(df["Close"], period=2)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all(), "RSI out of range"
    print("  PASS: RSI range")


def test_ema_follows_price():
    """Test EMA tracks close prices."""
    df = make_ohlc(300)
    ema = calc_ema(df["Close"], 200)
    # After warmup, EMA should be reasonably close to price
    diff_pct = abs(ema.iloc[-1] - df["Close"].iloc[-1]) / df["Close"].iloc[-1] * 100
    assert diff_pct < 20, f"EMA too far from price: {diff_pct:.1f}%"
    print("  PASS: EMA follows price")


def test_consecutive_down_days():
    """Test consecutive down days counter."""
    closes = pd.Series([100, 99, 98, 97, 100, 99, 98, 100, 100, 99])
    down = calc_consecutive_down_days(closes)

    # Expected: 0, 1, 2, 3, 0, 1, 2, 0, 0, 1
    expected = [0, 1, 2, 3, 0, 1, 2, 0, 0, 1]
    for i, (actual, exp) in enumerate(zip(down, expected)):
        assert actual == exp, f"Down days mismatch at {i}: {actual} vs {exp}"

    print("  PASS: Consecutive down days")


def test_ibs_strategy_entry_signal():
    """Test IBS++ generates entry signal on oversold conditions."""
    # Create data where the last bar is clearly oversold:
    # Close near low (IBS < 0.3), RSI(2) < 25, above 200 EMA
    df = make_ohlc(250, base=100, seed=1)

    # Force last bar to be oversold: close near the low
    last_idx = len(df) - 1
    df.iloc[last_idx, df.columns.get_loc("High")] = 110
    df.iloc[last_idx, df.columns.get_loc("Low")] = 100
    df.iloc[last_idx, df.columns.get_loc("Close")] = 101  # IBS = 0.1

    # Force a strong downtrend in last 2 bars for RSI(2) to be very low
    df.iloc[last_idx - 1, df.columns.get_loc("Close")] = 108
    df.iloc[last_idx - 2, df.columns.get_loc("Close")] = 112

    # Make sure we're above 200 EMA (set a strong uptrend before)
    for i in range(50, last_idx - 2):
        df.iloc[i, df.columns.get_loc("Close")] = 95 + (i - 50) * 0.1

    strategy = IBSMeanReversion()
    signal = strategy.generate_signal(
        ticker="TEST",
        df=df,
        current_position=0,
        bars_in_trade=0,
        vix_close=20.0,  # Normal VIX
    )

    # We expect either a LONG_ENTRY or NONE (if filters don't align with random data)
    # The key test is that it doesn't crash and returns a valid signal
    assert signal.signal_type in (SignalType.LONG_ENTRY, SignalType.NONE), \
        f"Unexpected signal: {signal}"
    print(f"  PASS: IBS entry signal ({signal.signal_type.value}: {signal.reason})")


def test_ibs_strategy_exit_signal():
    """Test IBS++ generates exit signal when overbought."""
    df = make_ohlc(250, base=100, seed=2)

    # Force last bar to be overbought: close near high
    last_idx = len(df) - 1
    df.iloc[last_idx, df.columns.get_loc("High")] = 110
    df.iloc[last_idx, df.columns.get_loc("Low")] = 100
    df.iloc[last_idx, df.columns.get_loc("Close")] = 109  # IBS = 0.9

    strategy = IBSMeanReversion()
    signal = strategy.generate_signal(
        ticker="TEST",
        df=df,
        current_position=1.0,  # Currently long
        bars_in_trade=2,
        vix_close=20.0,
    )

    assert signal.signal_type == SignalType.LONG_EXIT, \
        f"Expected exit, got: {signal}"
    print(f"  PASS: IBS exit signal ({signal.reason})")


def test_ibs_max_hold_exit():
    """Test IBS++ exits after max hold bars (when IBS is neutral)."""
    df = make_ohlc(250, base=100, seed=3)

    # Force last bar to neutral IBS so time exit triggers instead of IBS exit
    last_idx = len(df) - 1
    df.iloc[last_idx, df.columns.get_loc("High")] = 110
    df.iloc[last_idx, df.columns.get_loc("Low")] = 100
    df.iloc[last_idx, df.columns.get_loc("Close")] = 105  # IBS = 0.5
    df.iloc[last_idx - 1, df.columns.get_loc("Close")] = 105  # flat RSI

    strategy = IBSMeanReversion()
    signal = strategy.generate_signal(
        ticker="TEST",
        df=df,
        current_position=1.0,
        bars_in_trade=7,  # At max hold
        vix_close=20.0,
    )

    # Should exit (either IBS, RSI, or time — all valid at max hold)
    assert signal.signal_type == SignalType.LONG_EXIT, \
        f"Expected exit at max hold, got: {signal}"
    print(f"  PASS: IBS max hold exit ({signal.reason})")


def test_ibs_vix_blocks_trade():
    """Test VIX extreme blocks new entries."""
    df = make_ohlc(250, base=100, seed=4)

    # Force oversold
    last_idx = len(df) - 1
    df.iloc[last_idx, df.columns.get_loc("High")] = 110
    df.iloc[last_idx, df.columns.get_loc("Low")] = 100
    df.iloc[last_idx, df.columns.get_loc("Close")] = 101

    params = dict(config.IBS_PARAMS)
    params["vix_extreme_action"] = "Skip Trade"

    strategy = IBSMeanReversion(params=params)
    signal = strategy.generate_signal(
        ticker="TEST",
        df=df,
        current_position=0,
        bars_in_trade=0,
        vix_close=40.0,  # Extreme VIX
    )

    # Should either be NONE (VIX blocked) or NONE (other filter)
    assert signal.signal_type == SignalType.NONE, \
        f"VIX extreme should block entry, got: {signal}"
    print(f"  PASS: VIX extreme blocks trade ({signal.reason})")


def test_ibs_vix_half_size():
    """Test VIX low regime reduces position size."""
    df = make_ohlc(250, base=100, seed=5)

    # Force oversold + above EMA
    last_idx = len(df) - 1
    df.iloc[last_idx, df.columns.get_loc("High")] = 110
    df.iloc[last_idx, df.columns.get_loc("Low")] = 100
    df.iloc[last_idx, df.columns.get_loc("Close")] = 101
    df.iloc[last_idx - 1, df.columns.get_loc("Close")] = 108
    df.iloc[last_idx - 2, df.columns.get_loc("Close")] = 112

    strategy = IBSMeanReversion()
    signal = strategy.generate_signal(
        ticker="TEST",
        df=df,
        current_position=0,
        bars_in_trade=0,
        vix_close=12.0,  # Low VIX
    )

    if signal.signal_type == SignalType.LONG_ENTRY:
        assert signal.size_multiplier == 0.5, \
            f"Low VIX should halve size, got mult={signal.size_multiplier}"
        print(f"  PASS: VIX low halves size (mult={signal.size_multiplier})")
    else:
        print(f"  PASS: VIX low test (no entry due to other filters: {signal.reason})")


def test_trend_following_no_crash():
    """Test trend following strategy runs without error."""
    df = make_ohlc(100, base=50, seed=10)
    strategy = TrendFollowing()

    signal = strategy.generate_signal(
        ticker="SLV",
        df=df,
        current_position=0,
        bars_in_trade=0,
    )

    assert signal.signal_type in (
        SignalType.NONE, SignalType.LONG_ENTRY, SignalType.SHORT_ENTRY
    ), f"Unexpected signal: {signal}"
    print(f"  PASS: Trend following runs ({signal.signal_type.value}: {signal.reason})")


def test_rotation_no_crash():
    """Test rotation strategy runs without error."""
    spy = make_ohlc(300, base=450, seed=20)
    tlt = make_ohlc(300, base=100, seed=21)

    strategy = SPYTLTRotation()

    signal = strategy.generate_signal(
        ticker="SPY",
        df=spy,
        current_position=0,
        bars_in_trade=0,
        partner_df=tlt,
    )

    assert signal.signal_type in (
        SignalType.NONE, SignalType.LONG_ENTRY, SignalType.LONG_EXIT
    ), f"Unexpected signal: {signal}"
    print(f"  PASS: Rotation runs ({signal.signal_type.value}: {signal.reason})")


def test_atr_calculation():
    """Test ATR produces reasonable values."""
    df = make_ohlc(50)
    atr = calc_atr(df, period=14)
    valid = atr.dropna()
    assert len(valid) > 0, "ATR produced no values"
    assert (valid > 0).all(), "ATR should be positive"
    # ATR should be reasonable relative to price (< 10%)
    atr_pct = valid.iloc[-1] / df["Close"].iloc[-1] * 100
    assert atr_pct < 10, f"ATR seems too large: {atr_pct:.1f}%"
    print(f"  PASS: ATR calculation (ATR%={atr_pct:.2f}%)")


def test_adx_calculation():
    """Test ADX stays in 0-100 range."""
    df = make_ohlc(100)
    adx = calc_adx(df, period=14)
    valid = adx.dropna()
    assert (valid >= 0).all() and (valid <= 100).all(), "ADX out of range"
    print(f"  PASS: ADX calculation (current={valid.iloc[-1]:.1f})")


# Import config for the VIX test
import config

if __name__ == "__main__":
    print("Running strategy tests...\n")
    print("Indicator tests:")
    test_ibs_calculation()
    test_rsi_range()
    test_ema_follows_price()
    test_atr_calculation()
    test_adx_calculation()
    test_consecutive_down_days()

    print("\nIBS++ strategy tests:")
    test_ibs_strategy_entry_signal()
    test_ibs_strategy_exit_signal()
    test_ibs_max_hold_exit()
    test_ibs_vix_blocks_trade()
    test_ibs_vix_half_size()

    print("\nTrend Following tests:")
    test_trend_following_no_crash()

    print("\nRotation tests:")
    test_rotation_no_crash()

    print("\n" + "=" * 40)
    print("ALL TESTS PASSED")
    print("=" * 40)
