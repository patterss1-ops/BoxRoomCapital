"""
Verify position sizing calculations with worked examples.
Run: python3 verify_sizing.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from data.provider import DataProvider
from portfolio.risk import calc_position_size, RISK_PARAMS

data = DataProvider(lookback_days=500)
equity = 10000.0

print("=" * 70)
print(f"POSITION SIZING VERIFICATION — Equity: £{equity:,.0f}")
print(f"Risk per trade: {RISK_PARAMS['risk_per_trade_pct']}% = £{equity * RISK_PARAMS['risk_per_trade_pct'] / 100:,.0f}")
print("=" * 70)

test_cases = [
    ("SPY", "IBS++ v3"),
    ("QQQ", "IBS++ v3"),
    ("CL=F", "IBS++ v3"),
    ("GBPUSD=X", "IBS++ v3"),
    ("GC=F_trend", "Trend Following v2"),
    ("SI=F", "Trend Following v2"),
    ("NG=F", "Trend Following v2"),
    ("SPY", "SPY/TLT Rotation v3"),
]

print(f"\n{'Ticker':<12} {'Strategy':<22} {'Price':>8} {'ATR':>8} {'Stop':>8} "
      f"{'Stake':>8} {'Risk £':>8} {'Risk%':>6} {'Margin':>8} {'Notes'}")
print("─" * 120)

for ticker, strategy in test_cases:
    data_ticker = ticker.replace("_trend", "")
    df = data.get_daily_bars(data_ticker)
    if df.empty:
        print(f"{ticker:<12} {'NO DATA'}")
        continue

    result = calc_position_size(
        ticker=ticker,
        strategy_name=strategy,
        df=df,
        equity=equity,
    )

    price = df["Close"].iloc[-1]
    from portfolio.risk import calc_atr
    atr = calc_atr(df, 14)

    print(
        f"{ticker:<12} {strategy:<22} {price:>8.2f} {atr:>8.2f} "
        f"{result.stop_distance:>8.1f} £{result.stake_per_point:>6.2f} "
        f"£{result.risk_amount:>7.2f} {result.risk_pct_of_equity:>5.1f}% "
        f"£{result.margin_required:>7.0f} {result.notes}"
    )

print("\n" + "=" * 70)
print("FORMULA CHECK (manual calculation for SPY):")
df_spy = data.get_daily_bars("SPY")
if not df_spy.empty:
    spy_price = df_spy["Close"].iloc[-1]
    spy_atr = calc_atr(df_spy, 14)
    stop_dist = spy_atr * 2.0  # IBS++ uses 2x ATR
    risk_budget = equity * 0.01  # 1%
    calc_stake = risk_budget / stop_dist

    print(f"  SPY price:       {spy_price:.2f}")
    print(f"  ATR(14):         {spy_atr:.2f}")
    print(f"  Stop (2x ATR):   {stop_dist:.2f} points")
    print(f"  Risk budget:     £{risk_budget:.0f} (1% of £{equity:,.0f})")
    print(f"  Stake:           £{risk_budget:.0f} / {stop_dist:.2f} = £{calc_stake:.2f}/pt")
    print(f"  Margin (5%):     £{calc_stake * spy_price * 0.05:.0f}")
    print(f"  Notional:        £{calc_stake * spy_price:.0f}")
print("=" * 70)
