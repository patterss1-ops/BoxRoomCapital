#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class GridConfig:
    band: float
    levels: int
    fee_bps: float
    reset_on_breakout: bool
    reset_cooldown_hours: int = 24


@dataclass
class BacktestResult:
    strategy: str
    window_label: str
    start: str
    end: str
    bars: int
    start_nav: float
    end_nav: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe_annual: float
    turnover: float
    trades: int
    resets: int
    band: float
    levels: int
    fee_bps: float


def fetch_hourly_btc(period: str = "730d", interval: str = "1h") -> pd.Series:
    df = yf.download("BTC-USD", period=period, interval=interval, auto_adjust=False, progress=False)
    if df.empty:
        raise RuntimeError("No data returned from yfinance for BTC-USD")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        if close.shape[1] == 0:
            raise RuntimeError("Close column returned empty DataFrame")
        close = close.iloc[:, 0]
    close = close.dropna()
    close.index = pd.to_datetime(close.index, utc=True)
    return close


def cagr(start_nav: float, end_nav: float, bars: int, bars_per_year: float) -> float:
    if start_nav <= 0 or end_nav <= 0 or bars <= 1:
        return 0.0
    years = bars / bars_per_year
    if years <= 0:
        return 0.0
    return (end_nav / start_nav) ** (1 / years) - 1.0


def max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def sharpe_annual(nav: pd.Series, bars_per_year: float) -> float:
    rets = nav.pct_change().dropna()
    if rets.empty:
        return 0.0
    vol = rets.std()
    if vol <= 1e-12:
        return 0.0
    return float((rets.mean() / vol) * math.sqrt(bars_per_year))


def estimate_bars_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 365.25
    diffs = pd.Series(index[1:] - index[:-1]).dt.total_seconds().dropna()
    if diffs.empty:
        return 365.25
    median_seconds = float(diffs.median())
    if median_seconds <= 0:
        return 365.25
    return (365.25 * 24 * 3600) / median_seconds


def select_windows(close: pd.Series, window_bars: int) -> Dict[str, Tuple[pd.Timestamp, pd.Timestamp]]:
    if len(close) <= window_bars + 1:
        return {"full": (close.index[0], close.index[-1])}

    n = len(close)
    arr = close.values
    rolling_ret = arr[window_bars:] / arr[:-window_bars] - 1.0

    up_idx = int(np.argmax(rolling_ret))
    dn_idx = int(np.argmin(rolling_ret))
    range_idx = int(np.argmin(np.abs(rolling_ret)))

    def to_window(i: int) -> Tuple[pd.Timestamp, pd.Timestamp]:
        start = close.index[i]
        end = close.index[min(i + window_bars, n - 1)]
        return start, end

    return {
        "full": (close.index[0], close.index[-1]),
        "trend_up_window": to_window(up_idx),
        "trend_down_window": to_window(dn_idx),
        "range_like_window": to_window(range_idx),
    }


def buy_hold(close: pd.Series, initial_capital: float = 100_000.0) -> Tuple[pd.Series, float]:
    p0 = float(close.iloc[0])
    btc = initial_capital / p0
    nav = btc * close
    turnover = 1.0
    return nav, turnover


def _crossed_levels(prev_p: float, p: float, levels: np.ndarray) -> Tuple[int, int]:
    if p == prev_p:
        return 0, 0
    lo, hi = (prev_p, p) if p > prev_p else (p, prev_p)
    crossed = np.count_nonzero((levels > lo) & (levels <= hi))
    if p > prev_p:
        return crossed, 0
    return 0, crossed


def run_grid(close: pd.Series, cfg: GridConfig, initial_capital: float = 100_000.0) -> Tuple[pd.Series, float, int, int]:
    fee = cfg.fee_bps / 10_000.0
    p0 = float(close.iloc[0])

    cash = initial_capital * 0.5
    btc = (initial_capital * 0.5) / p0
    traded_notional = 0.0
    trades = 0
    resets = 0

    def build_grid(center_price: float) -> np.ndarray:
        lower = center_price * (1.0 - cfg.band)
        upper = center_price * (1.0 + cfg.band)
        return np.linspace(lower, upper, cfg.levels + 1)

    levels = build_grid(p0)
    # One execution unit per crossed level. Sized to utilize capital roughly across half-grid one-way move.
    units_half = max(cfg.levels // 2, 1)
    unit_btc = (initial_capital * 0.5 / p0) / units_half

    nav_vals = []
    idx_vals = []
    last_reset_time = close.index[0]

    prev_p = float(close.iloc[0])
    nav_vals.append(cash + btc * prev_p)
    idx_vals.append(close.index[0])

    for ts, p_val in close.iloc[1:].items():
        p = float(p_val)
        sells, buys = _crossed_levels(prev_p, p, levels)

        # Execute sells on upward crossings
        for _ in range(sells):
            if btc < unit_btc:
                break
            notional = unit_btc * p
            cash += notional * (1.0 - fee)
            btc -= unit_btc
            traded_notional += notional
            trades += 1

        # Execute buys on downward crossings
        for _ in range(buys):
            notional = unit_btc * p
            gross = notional * (1.0 + fee)
            if cash < gross:
                break
            cash -= gross
            btc += unit_btc
            traded_notional += notional
            trades += 1

        # Optional dynamic reset when price leaves band
        if cfg.reset_on_breakout:
            lower = levels[0]
            upper = levels[-1]
            outside = p < lower or p > upper
            cooldown_elapsed = (ts - last_reset_time).total_seconds() >= cfg.reset_cooldown_hours * 3600
            if outside and cooldown_elapsed:
                nav = cash + btc * p
                target_cash = nav * 0.5
                target_btc = (nav * 0.5) / p
                delta_btc = target_btc - btc
                if abs(delta_btc) > 0:
                    notional = abs(delta_btc) * p
                    traded_notional += notional
                    trades += 1
                    if delta_btc > 0:  # buy btc
                        cost = notional * (1.0 + fee)
                        if cost <= cash:
                            cash -= cost
                            btc += delta_btc
                    else:  # sell btc
                        qty = min(abs(delta_btc), btc)
                        received = qty * p * (1.0 - fee)
                        cash += received
                        btc -= qty
                levels = build_grid(p)
                last_reset_time = ts
                resets += 1

        nav_vals.append(cash + btc * p)
        idx_vals.append(ts)
        prev_p = p

    nav = pd.Series(nav_vals, index=pd.DatetimeIndex(idx_vals))
    turnover = traded_notional / initial_capital
    return nav, turnover, trades, resets


def evaluate_window(
    close: pd.Series,
    window_label: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    grid_cfgs: List[GridConfig],
) -> List[BacktestResult]:
    sub = close.loc[start:end]
    if len(sub) < 50:
        return []

    bars_per_year = estimate_bars_per_year(sub.index)
    out: List[BacktestResult] = []

    bh_nav, bh_turnover = buy_hold(sub)
    out.append(
        BacktestResult(
            strategy="buy_hold",
            window_label=window_label,
            start=str(sub.index[0]),
            end=str(sub.index[-1]),
            bars=len(sub),
            start_nav=float(bh_nav.iloc[0]),
            end_nav=float(bh_nav.iloc[-1]),
            total_return_pct=float((bh_nav.iloc[-1] / bh_nav.iloc[0] - 1.0) * 100.0),
            cagr_pct=float(cagr(float(bh_nav.iloc[0]), float(bh_nav.iloc[-1]), len(sub), bars_per_year) * 100.0),
            max_drawdown_pct=float(max_drawdown(bh_nav) * 100.0),
            sharpe_annual=float(sharpe_annual(bh_nav, bars_per_year)),
            turnover=float(bh_turnover),
            trades=1,
            resets=0,
            band=0.0,
            levels=0,
            fee_bps=0.0,
        )
    )

    for cfg in grid_cfgs:
        nav, turnover, trades, resets = run_grid(sub, cfg)
        out.append(
            BacktestResult(
                strategy="grid_dynamic" if cfg.reset_on_breakout else "grid_static",
                window_label=window_label,
                start=str(sub.index[0]),
                end=str(sub.index[-1]),
                bars=len(sub),
                start_nav=float(nav.iloc[0]),
                end_nav=float(nav.iloc[-1]),
                total_return_pct=float((nav.iloc[-1] / nav.iloc[0] - 1.0) * 100.0),
                cagr_pct=float(cagr(float(nav.iloc[0]), float(nav.iloc[-1]), len(sub), bars_per_year) * 100.0),
                max_drawdown_pct=float(max_drawdown(nav) * 100.0),
                sharpe_annual=float(sharpe_annual(nav, bars_per_year)),
                turnover=float(turnover),
                trades=int(trades),
                resets=int(resets),
                band=float(cfg.band),
                levels=int(cfg.levels),
                fee_bps=float(cfg.fee_bps),
            )
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid strategy deep-dive backtest on BTC hourly data")
    parser.add_argument("--period", default="730d", help="yfinance period, e.g. 730d")
    parser.add_argument("--interval", default="1h", help="yfinance interval")
    parser.add_argument("--out-json", default=".runtime/grid_backtest_results.json", help="Output JSON path")
    parser.add_argument("--out-csv", default=".runtime/grid_backtest_results.csv", help="Output CSV path")
    args = parser.parse_args()

    close = fetch_hourly_btc(period=args.period, interval=args.interval)
    bars_per_year = estimate_bars_per_year(close.index)
    # Approx 60d for hourly bars, ~120d for daily bars.
    if bars_per_year > 2000:
        window_bars = 24 * 60
    else:
        window_bars = 120
    windows = select_windows(close, window_bars=window_bars)

    cfgs = [
        GridConfig(band=0.10, levels=20, fee_bps=4.0, reset_on_breakout=False),
        GridConfig(band=0.20, levels=40, fee_bps=4.0, reset_on_breakout=False),
        GridConfig(band=0.30, levels=80, fee_bps=4.0, reset_on_breakout=False),
        GridConfig(band=0.10, levels=20, fee_bps=10.0, reset_on_breakout=False),
        GridConfig(band=0.20, levels=40, fee_bps=10.0, reset_on_breakout=False),
        GridConfig(band=0.30, levels=80, fee_bps=10.0, reset_on_breakout=False),
        GridConfig(band=0.20, levels=40, fee_bps=4.0, reset_on_breakout=True),
        GridConfig(band=0.20, levels=40, fee_bps=10.0, reset_on_breakout=True),
    ]

    rows: List[BacktestResult] = []
    for label, (start, end) in windows.items():
        rows.extend(evaluate_window(close, label, start, end, cfgs))

    df = pd.DataFrame([r.__dict__ for r in rows])
    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(out_json, orient="records", indent=2)
    df.to_csv(out_csv, index=False)

    print(f"Wrote {len(df)} rows to {out_json} and {out_csv}")
    print(df.groupby(["window_label", "strategy"])[["total_return_pct", "max_drawdown_pct", "sharpe_annual"]].mean())


if __name__ == "__main__":
    main()
