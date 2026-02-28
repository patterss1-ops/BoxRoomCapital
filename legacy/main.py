"""
Trading Bot — Main Entry Point

Runs daily after market close. Fetches data, generates signals across all
strategies and markets, and executes orders via the configured broker.

Usage:
    python main.py              # Run once (paper mode by default)
    python main.py --live       # Run once with IG live
    python main.py --demo       # Run once with IG demo
    python main.py --backtest   # Run backtest validation
"""
import argparse
import logging
import sys
from datetime import datetime

import config
from data.provider import DataProvider
from strategies.ibs_mean_reversion import IBSMeanReversion
from strategies.trend_following import TrendFollowing
from strategies.spy_tlt_rotation import SPYTLTRotation
from strategies.base import SignalType
from broker.paper import PaperBroker
from broker.ig import IGBroker
from portfolio.manager import PortfolioManager
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


def create_broker(mode: str):
    """Create the appropriate broker based on mode."""
    if mode == "paper":
        return PaperBroker()
    elif mode == "demo":
        return IGBroker(is_demo=True)
    elif mode == "live":
        return IGBroker(is_demo=False)
    else:
        raise ValueError(f"Unknown broker mode: {mode}")


def run_daily():
    """
    Main daily run: fetch data, generate signals, execute orders.
    Called once per day after market close.
    """
    logger.info("=" * 60)
    logger.info(f"DAILY RUN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Broker mode: {config.BROKER_MODE}")
    logger.info("=" * 60)

    # ─── Setup ───────────────────────────────────────────────────────────
    data = DataProvider(lookback_days=500)  # 500 days for 200 EMA + 126 abs momentum
    broker = create_broker(config.BROKER_MODE)
    portfolio = PortfolioManager(broker, data_provider=data)

    # Connect to broker
    if not broker.connect():
        logger.error("Failed to connect to broker. Aborting.")
        return

    try:
        # Increment bars held for existing positions
        portfolio.increment_bars()

        # Fetch VIX data (used by IBS++ strategy)
        vix_df = data.get_vix()
        vix_close = vix_df["Close"].iloc[-1] if not vix_df.empty else None
        if vix_close is not None:
            logger.info(f"VIX: {vix_close:.2f}")

        # ─── Strategy 1: IBS++ v3 (Mean Reversion) ──────────────────────
        logger.info("─── IBS++ Mean Reversion ───")
        ibs_strategy = IBSMeanReversion()

        ibs_tickers = [
            t for t, m in config.MARKET_MAP.items()
            if m["strategy"] == "ibs"
        ]

        for ticker in ibs_tickers:
            df = data.get_daily_bars(ticker)
            if df.empty:
                logger.warning(f"No data for {ticker}, skipping")
                continue

            pos_size = portfolio.get_position_size(ticker, ibs_strategy.name)
            bars_held = portfolio.get_bars_in_trade(ticker, ibs_strategy.name)

            signal = ibs_strategy.generate_signal(
                ticker=ticker,
                df=df,
                current_position=pos_size,
                bars_in_trade=bars_held,
                vix_close=vix_close,
            )

            if signal.signal_type != SignalType.NONE:
                logger.info(f"  {ticker}: {signal}")
                current_price = df["Close"].iloc[-1]
                portfolio.process_signal(signal, current_price)
            else:
                logger.debug(f"  {ticker}: {signal.reason}")

        # ─── Strategy 2: Trend Following v2 ─────────────────────────────
        logger.info("─── Trend Following ───")
        trend_strategy = TrendFollowing()

        trend_tickers = [
            t for t, m in config.MARKET_MAP.items()
            if m["strategy"] == "trend"
        ]

        for ticker in trend_tickers:
            # Strip _trend suffix for data fetching
            data_ticker = ticker.replace("_trend", "")
            df = data.get_daily_bars(data_ticker)
            if df.empty:
                logger.warning(f"No data for {data_ticker}, skipping")
                continue

            pos_size = portfolio.get_position_size(ticker, trend_strategy.name)
            bars_held = portfolio.get_bars_in_trade(ticker, trend_strategy.name)

            signal = trend_strategy.generate_signal(
                ticker=ticker,
                df=df,
                current_position=pos_size,
                bars_in_trade=bars_held,
            )

            if signal.signal_type != SignalType.NONE:
                logger.info(f"  {data_ticker}: {signal}")
                current_price = df["Close"].iloc[-1]
                portfolio.process_signal(signal, current_price)
            else:
                logger.debug(f"  {data_ticker}: {signal.reason}")

        # ─── Strategy 3: SPY/TLT Rotation v3 ────────────────────────────
        logger.info("─── SPY/TLT Rotation ───")
        rotation_strategy = SPYTLTRotation()

        spy_df = data.get_daily_bars(config.ROTATION_TICKERS["primary"])
        tlt_df = data.get_daily_bars(config.ROTATION_TICKERS["partner"])

        if not spy_df.empty and not tlt_df.empty:
            primary = config.ROTATION_TICKERS["primary"]
            pos_size = portfolio.get_position_size(primary, rotation_strategy.name)
            bars_held = portfolio.get_bars_in_trade(primary, rotation_strategy.name)

            signal = rotation_strategy.generate_signal(
                ticker=primary,
                df=spy_df,
                current_position=pos_size,
                bars_in_trade=bars_held,
                partner_df=tlt_df,
            )

            if signal.signal_type != SignalType.NONE:
                logger.info(f"  {primary}: {signal}")
                current_price = spy_df["Close"].iloc[-1]
                portfolio.process_signal(signal, current_price)
            else:
                logger.debug(f"  {primary}: {signal.reason}")
        else:
            logger.warning("Missing SPY or TLT data for rotation strategy")

        # ─── Summary ────────────────────────────────────────────────────
        logger.info("")
        logger.info(portfolio.daily_summary())

        if isinstance(broker, PaperBroker):
            logger.info("")
            logger.info(broker.summary())

    finally:
        broker.disconnect()

    logger.info("Daily run complete.")


def run_backtest():
    """
    Run a simple historical backtest to validate strategy logic.
    Compares against known Pine Script results.
    """
    logger.info("=" * 60)
    logger.info("BACKTEST VALIDATION MODE")
    logger.info("=" * 60)

    data = DataProvider(lookback_days=500)
    broker = PaperBroker(initial_capital=10000)
    portfolio = PortfolioManager(broker, data_provider=data)
    broker.connect()

    # Test IBS++ on QQQ
    logger.info("Testing IBS++ on QQQ...")
    ibs = IBSMeanReversion()
    df = data.get_daily_bars("QQQ")
    vix_df = data.get_vix()

    if df.empty:
        logger.error("Could not fetch QQQ data")
        return

    signals_generated = 0
    trades_opened = 0
    trades_closed = 0

    # Walk through the data bar by bar (simple forward-only backtest)
    min_bars = config.IBS_PARAMS["ema_period"] + 20
    for i in range(min_bars, len(df)):
        historical_df = df.iloc[:i + 1]
        vix_close = None
        if not vix_df.empty and i < len(vix_df):
            vix_close = vix_df["Close"].iloc[min(i, len(vix_df) - 1)]

        pos_size = portfolio.get_position_size("QQQ", ibs.name)
        bars_held = portfolio.get_bars_in_trade("QQQ", ibs.name)

        signal = ibs.generate_signal(
            ticker="QQQ",
            df=historical_df,
            current_position=pos_size,
            bars_in_trade=bars_held,
            vix_close=vix_close,
        )

        if signal.signal_type != SignalType.NONE:
            signals_generated += 1
            current_price = historical_df["Close"].iloc[-1]
            executed = portfolio.process_signal(signal, current_price)
            if executed:
                if signal.signal_type == SignalType.LONG_ENTRY:
                    trades_opened += 1
                elif signal.signal_type == SignalType.LONG_EXIT:
                    trades_closed += 1

        # Increment bars each "day"
        portfolio.increment_bars()

    logger.info(f"\nBacktest Results (QQQ, ~{len(df) - min_bars} bars):")
    logger.info(f"  Signals generated: {signals_generated}")
    logger.info(f"  Trades opened: {trades_opened}")
    logger.info(f"  Trades closed: {trades_closed}")
    logger.info(f"  Still open: {len(portfolio.positions)}")
    logger.info(broker.summary())

    broker.disconnect()


def run_dry_run():
    """
    Dry run: connect to IG, fetch real data, generate signals, but DON'T place any orders.
    Shows exactly what the bot would do today.
    """
    logger.info("=" * 60)
    logger.info(f"DRY RUN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("Signals will be generated but NO orders placed.")
    logger.info("=" * 60)

    data = DataProvider(lookback_days=500)

    # Connect to IG just to verify connectivity and show account info
    broker = IGBroker(is_demo=False)
    if broker.connect():
        acct = broker.get_account_info()
        logger.info(f"Account balance: £{acct.balance:.2f}")
        logger.info(f"Unrealised P&L: £{acct.unrealised_pnl:.2f}")
        positions = broker.get_positions()
        logger.info(f"Open positions: {len(positions)}")
        for p in positions:
            logger.info(f"  {p.direction.upper()} {p.ticker} @ {p.entry_price} (P&L: £{p.unrealised_pnl:.2f})")

        # Verify all market EPICs
        logger.info("\nVerifying market access...")
        results = broker.verify_markets()
        ok = sum(1 for v in results.values() if v)
        fail = sum(1 for v in results.values() if not v)
        logger.info(f"Markets: {ok} accessible, {fail} blocked")
        if fail > 0:
            blocked = [t for t, v in results.items() if not v]
            logger.warning(f"Blocked: {', '.join(blocked)} — run discover_epics.py to fix")
    else:
        logger.warning("Could not connect to IG — proceeding with signal generation only")

    # Fetch VIX
    vix_df = data.get_vix()
    vix_close = vix_df["Close"].iloc[-1] if not vix_df.empty else None
    logger.info(f"\nVIX: {vix_close:.2f}" if vix_close else "\nVIX: unavailable")

    signals_today = []

    # ─── IBS++ ────────────────────────────────────────────────────────────
    logger.info("\n─── IBS++ Mean Reversion Signals ───")
    ibs = IBSMeanReversion()
    ibs_tickers = [t for t, m in config.MARKET_MAP.items() if m["strategy"] == "ibs"]

    for ticker in ibs_tickers:
        df = data.get_daily_bars(ticker)
        if df.empty:
            logger.warning(f"  {ticker}: no data")
            continue

        signal = ibs.generate_signal(ticker=ticker, df=df, current_position=0,
                                     bars_in_trade=0, vix_close=vix_close)
        price = df["Close"].iloc[-1]
        ibs_val = (df["Close"].iloc[-1] - df["Low"].iloc[-1]) / max(df["High"].iloc[-1] - df["Low"].iloc[-1], 0.001)

        if signal.signal_type != SignalType.NONE:
            logger.info(f"  >> {ticker}: {signal.signal_type.name} — {signal.reason} (price={price:.2f}, IBS={ibs_val:.3f})")
            signals_today.append(signal)
        else:
            logger.info(f"  {ticker}: no signal (price={price:.2f}, IBS={ibs_val:.3f}) — {signal.reason}")

    # ─── Trend Following ──────────────────────────────────────────────────
    logger.info("\n─── Trend Following Signals ───")
    trend = TrendFollowing()
    trend_tickers = [t for t, m in config.MARKET_MAP.items() if m["strategy"] == "trend"]

    for ticker in trend_tickers:
        data_ticker = ticker.replace("_trend", "")
        df = data.get_daily_bars(data_ticker)
        if df.empty:
            logger.warning(f"  {data_ticker}: no data")
            continue

        signal = trend.generate_signal(ticker=ticker, df=df, current_position=0, bars_in_trade=0)
        price = df["Close"].iloc[-1]

        if signal.signal_type != SignalType.NONE:
            logger.info(f"  >> {data_ticker}: {signal.signal_type.name} — {signal.reason} (price={price:.2f})")
            signals_today.append(signal)
        else:
            logger.info(f"  {data_ticker}: no signal (price={price:.2f}) — {signal.reason}")

    # ─── SPY/TLT Rotation ────────────────────────────────────────────────
    logger.info("\n─── SPY/TLT Rotation Signals ───")
    rotation = SPYTLTRotation()
    spy_df = data.get_daily_bars("SPY")
    tlt_df = data.get_daily_bars("TLT")

    if not spy_df.empty and not tlt_df.empty:
        signal = rotation.generate_signal(ticker="SPY", df=spy_df, current_position=0,
                                          bars_in_trade=0, partner_df=tlt_df)
        if signal.signal_type != SignalType.NONE:
            logger.info(f"  >> SPY: {signal.signal_type.name} — {signal.reason}")
            signals_today.append(signal)
        else:
            logger.info(f"  SPY: no signal — {signal.reason}")

    # ─── Summary ──────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info(f"DRY RUN COMPLETE — {len(signals_today)} signal(s) generated")
    if signals_today:
        logger.info("Signals that WOULD execute:")
        for s in signals_today:
            logger.info(f"  {s.signal_type.name}: {s.ticker} [{s.strategy_name}] — {s.reason}")
    else:
        logger.info("No trades would be placed today.")
    logger.info("=" * 60)

    if broker.session:
        broker.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Multi-Strategy Trading Bot")
    parser.add_argument("--mode", choices=["paper", "demo", "live"], default=None,
                        help="Broker mode (overrides config)")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest validation")
    parser.add_argument("--dry-run", action="store_true",
                        help="Connect to IG, generate signals, but don't place orders")
    args = parser.parse_args()

    # Override broker mode if specified
    if args.mode:
        config.BROKER_MODE = args.mode

    setup_logging()

    if args.backtest:
        run_backtest()
    elif args.dry_run:
        run_dry_run()
    else:
        run_daily()


if __name__ == "__main__":
    main()
