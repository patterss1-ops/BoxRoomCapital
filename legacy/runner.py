"""
Continuous Trading Bot Runner

Keeps the bot running 24/7, checking signals at the right times:
- After European close (5:00pm UK) → check FTSE, DAX, Nikkei signals
- After US close (9:15pm UK) → check all US signals (SPY, QQQ, IWM, etc.)
- Position monitor every 5 minutes during market hours → exit triggers, stops
- Daily snapshot at 10pm UK
- Heartbeat every 15 minutes so you know it's alive

Usage:
    python3 runner.py                    # paper mode (default)
    python3 runner.py --mode live        # real money on IG
    python3 runner.py --mode paper       # simulated trades

Press Ctrl+C to stop gracefully.
"""
import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from data.provider import DataProvider
from strategies.ibs_mean_reversion import IBSMeanReversion
from strategies.trend_following import TrendFollowing
from strategies.spy_tlt_rotation import SPYTLTRotation
from strategies.base import SignalType
from broker.paper import PaperBroker
from broker.ig import IGBroker
from portfolio.manager import PortfolioManager
from data.trade_db import log_trade, save_daily_snapshot, log_event
from utils.logger import setup_logging

logger = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")

# ─── Market schedule (UK times) ──────────────────────────────────────────────

SCHEDULE = {
    "eu_close": {"hour": 17, "minute": 5, "label": "European close"},
    "us_close": {"hour": 21, "minute": 15, "label": "US close"},
    "daily_snapshot": {"hour": 22, "minute": 0, "label": "Daily snapshot"},
}

# Which tickers to check at each close
EU_TICKERS_IBS = ["EWU", "EWG", "EWJ"]
US_TICKERS_IBS = ["SPY", "QQQ", "IWM", "DIA", "IEF", "CL=F", "GBPUSD=X", "GC=F"]
TREND_TICKERS = ["SI=F", "GC=F_trend", "CL=F_trend", "NG=F", "HG=F"]

POSITION_CHECK_INTERVAL = 300  # 5 minutes
HEARTBEAT_INTERVAL = 900       # 15 minutes


class TradingBotRunner:
    """Continuous trading bot that monitors markets and executes signals."""

    def __init__(self, mode: str = "paper"):
        self.mode = mode
        self.running = False
        self.broker = None
        self.portfolio = None
        self.data = DataProvider(lookback_days=500)

        # Strategies
        self.ibs = IBSMeanReversion()
        self.trend = TrendFollowing()
        self.rotation = SPYTLTRotation()

        # Track what we've done today to avoid duplicate runs
        self._last_eu_check = None
        self._last_us_check = None
        self._last_snapshot = None
        self._last_heartbeat = None
        self._last_position_check = None

    def start(self):
        """Start the continuous runner."""
        logger.info("=" * 60)
        logger.info(f"TRADING BOT STARTING — {datetime.now(UK).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info(f"Mode: {self.mode.upper()}")
        logger.info(f"Strategies: IBS++ v3, Trend Following v2, SPY/TLT Rotation v3")
        logger.info("=" * 60)

        log_event("STARTUP", f"Bot started in {self.mode.upper()} mode",
                  f"Strategies: IBS++ v3, Trend Following v2, SPY/TLT Rotation v3")

        # Create broker
        if self.mode == "paper":
            self.broker = PaperBroker()
        elif self.mode == "live":
            self.broker = IGBroker(is_demo=False)
        elif self.mode == "demo":
            self.broker = IGBroker(is_demo=True)

        if not self.broker.connect():
            logger.error("Failed to connect to broker. Aborting.")
            return

        self.portfolio = PortfolioManager(self.broker, data_provider=self.data)

        # Verify all market EPICs are accessible
        if hasattr(self.broker, 'verify_markets'):
            logger.info("\nVerifying market access...")
            results = self.broker.verify_markets()
            ok = sum(1 for v in results.values() if v)
            fail = sum(1 for v in results.values() if not v)
            logger.info(f"Markets: {ok} accessible, {fail} blocked")
            log_event("STARTUP", f"Market verification: {ok} accessible, {fail} blocked",
                      f"Verified all {len(results)} configured markets against IG API")
            if fail > 0:
                blocked = [t for t, v in results.items() if not v]
                logger.warning(f"Blocked markets (will skip): {', '.join(blocked)}")
                logger.warning("Run: python3 discover_epics.py to find correct EPICs\n")
                log_event("ERROR", f"Blocked markets: {', '.join(blocked)}",
                          "These markets returned 403/404 and will be skipped")

        # Show initial status
        self._show_status()

        # Handle Ctrl+C gracefully
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        self.running = True

        # ─── Immediate first scan on startup ─────────────────────────────
        logger.info("\nRunning initial signal scan...")
        self._run_startup_scan()
        logger.info("\nBot is running. Press Ctrl+C to stop.\n")

        # Main loop
        while self.running:
            try:
                self._tick()
                time.sleep(30)  # Check every 30 seconds
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(60)  # Wait a minute before retrying

        self._shutdown_clean()

    def _tick(self):
        """Called every 30 seconds — decides what to do."""
        now = datetime.now(UK)
        today = now.date()

        # ─── Heartbeat ────────────────────────────────────────────────────
        if self._last_heartbeat is None or (now - self._last_heartbeat).seconds >= HEARTBEAT_INTERVAL:
            positions = len(self.portfolio.positions) if self.portfolio else 0
            logger.info(f"[heartbeat] {now.strftime('%H:%M')} UK | {positions} open positions | mode={self.mode}")
            log_event("HEARTBEAT", f"Bot alive — {positions} open positions",
                      f"{now.strftime('%H:%M')} UK | mode={self.mode}")
            self._last_heartbeat = now

        # ─── European close check (5:05pm UK) ────────────────────────────
        if (now.hour == SCHEDULE["eu_close"]["hour"]
            and now.minute >= SCHEDULE["eu_close"]["minute"]
            and self._last_eu_check != today):

            logger.info("\n" + "─" * 40)
            logger.info("EUROPEAN CLOSE — Checking EU signals")
            logger.info("─" * 40)
            log_event("SCAN", "European close scan started",
                      f"Checking IBS++ signals for {', '.join(EU_TICKERS_IBS)}")
            self._run_ibs_signals(EU_TICKERS_IBS)
            log_event("SCAN", "European close scan complete")
            self._last_eu_check = today

        # ─── US close check (9:15pm UK) ──────────────────────────────────
        if (now.hour == SCHEDULE["us_close"]["hour"]
            and now.minute >= SCHEDULE["us_close"]["minute"]
            and self._last_us_check != today):

            logger.info("\n" + "─" * 40)
            logger.info("US CLOSE — Checking all signals")
            logger.info("─" * 40)
            log_event("SCAN", "US close scan started",
                      f"Checking IBS++, Trend Following, and SPY/TLT Rotation across all markets")

            # Increment bars held (once per day, after US close)
            self.portfolio.increment_bars()

            # IBS++ on US tickers
            self._run_ibs_signals(US_TICKERS_IBS)

            # Trend following
            self._run_trend_signals()

            # SPY/TLT Rotation
            self._run_rotation_signal()

            log_event("SCAN", "US close scan complete")
            self._last_us_check = today

        # ─── Position monitoring (every 5 min during market hours) ────────
        if (8 <= now.hour <= 22
            and (self._last_position_check is None
                 or (now - self._last_position_check).seconds >= POSITION_CHECK_INTERVAL)):
            self._check_positions()
            self._last_position_check = now

        # ─── Daily snapshot (10pm UK) ─────────────────────────────────────
        if (now.hour == SCHEDULE["daily_snapshot"]["hour"]
            and self._last_snapshot != today):

            logger.info("\n" + "─" * 40)
            logger.info("END OF DAY — Saving snapshot")
            logger.info("─" * 40)
            summary = self.portfolio.daily_summary()
            logger.info(summary)
            log_event("SNAPSHOT", "Daily snapshot saved", summary)
            self._last_snapshot = today

    # ─── Startup scan ────────────────────────────────────────────────────

    def _run_startup_scan(self):
        """Run a full signal scan immediately on startup so we don't miss anything."""
        logger.info("\n" + "─" * 40)
        logger.info("STARTUP SCAN — Checking ALL signals now")
        logger.info("─" * 40)
        log_event("SCAN", "Startup scan — checking all signals across all markets",
                  "Running IBS++, Trend Following, and SPY/TLT Rotation on all configured markets")

        # Increment bars held
        self.portfolio.increment_bars()

        # Run all strategies
        all_ibs = EU_TICKERS_IBS + US_TICKERS_IBS
        self._run_ibs_signals(all_ibs)
        self._run_trend_signals()
        self._run_rotation_signal()

        # Mark today's checks as done so scheduled checks don't duplicate
        now = datetime.now(UK)
        today = now.date()

        # Only mark as done if we're past the scheduled time
        if now.hour >= SCHEDULE["eu_close"]["hour"]:
            self._last_eu_check = today
        if now.hour >= SCHEDULE["us_close"]["hour"]:
            self._last_us_check = today

        logger.info("Startup scan complete.")
        log_event("SCAN", "Startup scan complete")

    # ─── Signal runners ──────────────────────────────────────────────────

    def _run_ibs_signals(self, tickers: list[str]):
        """Run IBS++ strategy on given tickers."""
        vix_df = self.data.get_vix()
        vix_close = vix_df["Close"].iloc[-1] if not vix_df.empty else None
        if vix_close:
            logger.info(f"VIX: {vix_close:.2f}")

        for ticker in tickers:
            try:
                df = self.data.get_daily_bars(ticker)
                if df.empty:
                    logger.warning(f"  {ticker}: no data")
                    continue

                pos_size = self.portfolio.get_position_size(ticker, self.ibs.name)
                bars_held = self.portfolio.get_bars_in_trade(ticker, self.ibs.name)

                sig = self.ibs.generate_signal(
                    ticker=ticker, df=df, current_position=pos_size,
                    bars_in_trade=bars_held, vix_close=vix_close,
                )

                price = df["Close"].iloc[-1]
                ibs_val = (df["Close"].iloc[-1] - df["Low"].iloc[-1]) / max(df["High"].iloc[-1] - df["Low"].iloc[-1], 0.001)

                if sig.signal_type != SignalType.NONE:
                    logger.info(f"  >> {ticker}: {sig.signal_type.name} — {sig.reason} (IBS={ibs_val:.3f})")
                    log_event("SIGNAL",
                              f"{ticker} — {sig.signal_type.name} signal detected",
                              f"{sig.reason} | IBS={ibs_val:.3f}, RSI={sig.metadata.get('rsi', '?') if hasattr(sig, 'metadata') and sig.metadata else '?'}, VIX={vix_close:.1f}" if vix_close else sig.reason,
                              ticker=ticker, strategy="IBS++ v3")
                    result = self.portfolio.process_signal(sig, price)
                    if result and hasattr(result, 'success'):
                        if result.success:
                            log_event("ORDER",
                                      f"{ticker} — {sig.signal_type.name} order filled @ {result.fill_price or price:.2f}",
                                      f"Size: £{result.fill_qty or 0:.2f}/pt | Deal: {result.order_id or '?'}",
                                      ticker=ticker, strategy="IBS++ v3")
                        else:
                            log_event("REJECTION",
                                      f"{ticker} — Order rejected: {result.message}",
                                      f"Attempted {sig.signal_type.name} at price {price:.2f}",
                                      ticker=ticker, strategy="IBS++ v3")
                else:
                    logger.debug(f"  {ticker}: no signal (IBS={ibs_val:.3f})")

            except Exception as e:
                logger.error(f"  {ticker} error: {e}")
                log_event("ERROR", f"{ticker} — Error in IBS++ signal check",
                          str(e), ticker=ticker, strategy="IBS++ v3")

    def _run_trend_signals(self):
        """Run Trend Following strategy."""
        logger.info("Trend Following signals:")
        for ticker in TREND_TICKERS:
            try:
                data_ticker = ticker.replace("_trend", "")
                df = self.data.get_daily_bars(data_ticker)
                if df.empty:
                    continue

                pos_size = self.portfolio.get_position_size(ticker, self.trend.name)
                bars_held = self.portfolio.get_bars_in_trade(ticker, self.trend.name)

                sig = self.trend.generate_signal(
                    ticker=ticker, df=df, current_position=pos_size,
                    bars_in_trade=bars_held,
                )

                if sig.signal_type != SignalType.NONE:
                    price = df["Close"].iloc[-1]
                    logger.info(f"  >> {data_ticker}: {sig.signal_type.name} — {sig.reason}")
                    log_event("SIGNAL",
                              f"{data_ticker} — {sig.signal_type.name} signal detected",
                              sig.reason, ticker=ticker, strategy="Trend Following v2")
                    result = self.portfolio.process_signal(sig, price)
                    if result and hasattr(result, 'success'):
                        if result.success:
                            log_event("ORDER",
                                      f"{data_ticker} — {sig.signal_type.name} order filled @ {result.fill_price or price:.2f}",
                                      f"Size: £{result.fill_qty or 0:.2f}/pt",
                                      ticker=ticker, strategy="Trend Following v2")
                        else:
                            log_event("REJECTION",
                                      f"{data_ticker} — Order rejected: {result.message}",
                                      f"Attempted {sig.signal_type.name}",
                                      ticker=ticker, strategy="Trend Following v2")
                else:
                    logger.debug(f"  {data_ticker}: no signal")

            except Exception as e:
                logger.error(f"  {ticker} error: {e}")
                log_event("ERROR", f"{ticker} — Error in trend signal check",
                          str(e), ticker=ticker, strategy="Trend Following v2")

    def _run_rotation_signal(self):
        """Run SPY/TLT Rotation strategy."""
        logger.info("SPY/TLT Rotation:")
        try:
            spy_df = self.data.get_daily_bars("SPY")
            tlt_df = self.data.get_daily_bars("TLT")

            if spy_df.empty or tlt_df.empty:
                logger.warning("  Missing SPY or TLT data")
                log_event("ERROR", "SPY/TLT Rotation — Missing price data",
                          "Could not fetch SPY or TLT daily bars", strategy="SPY/TLT Rotation v3")
                return

            primary = config.ROTATION_TICKERS["primary"]
            pos_size = self.portfolio.get_position_size(primary, self.rotation.name)
            bars_held = self.portfolio.get_bars_in_trade(primary, self.rotation.name)

            sig = self.rotation.generate_signal(
                ticker=primary, df=spy_df, current_position=pos_size,
                bars_in_trade=bars_held, partner_df=tlt_df,
            )

            if sig.signal_type != SignalType.NONE:
                price = spy_df["Close"].iloc[-1]
                logger.info(f"  >> SPY: {sig.signal_type.name} — {sig.reason}")
                log_event("SIGNAL",
                          f"SPY — {sig.signal_type.name} signal detected",
                          sig.reason, ticker="SPY", strategy="SPY/TLT Rotation v3")
                result = self.portfolio.process_signal(sig, price)
                if result and hasattr(result, 'success'):
                    if result.success:
                        log_event("ORDER",
                                  f"SPY — {sig.signal_type.name} order filled @ {result.fill_price or price:.2f}",
                                  f"Size: £{result.fill_qty or 0:.2f}/pt",
                                  ticker="SPY", strategy="SPY/TLT Rotation v3")
                    else:
                        log_event("REJECTION",
                                  f"SPY — Order rejected: {result.message}",
                                  f"Attempted {sig.signal_type.name}",
                                  ticker="SPY", strategy="SPY/TLT Rotation v3")
            else:
                logger.debug(f"  SPY: no signal — {sig.reason}")

        except Exception as e:
            logger.error(f"  Rotation error: {e}")
            log_event("ERROR", "SPY/TLT Rotation — Error in signal check",
                      str(e), strategy="SPY/TLT Rotation v3")

    # ─── Position monitoring ─────────────────────────────────────────────

    def _check_positions(self):
        """Check open positions for exit conditions (max hold, stops, etc.)."""
        if not self.portfolio or not self.portfolio.positions:
            return

        for key, pos in list(self.portfolio.positions.items()):
            try:
                df = self.data.get_daily_bars(pos.ticker.replace("_trend", ""))
                if df.empty:
                    continue

                price = df["Close"].iloc[-1]

                # Check IBS++ max hold exit
                if pos.strategy == self.ibs.name:
                    max_hold = config.IBS_PARAMS["max_hold_bars"]
                    if pos.bars_held >= max_hold:
                        logger.info(f"  [MONITOR] {pos.ticker}: max hold ({max_hold} bars) reached — generating exit")
                        log_event("POSITION",
                                  f"{pos.ticker} — Max hold reached ({max_hold} bars), closing",
                                  f"Entry: {pos.entry_price:.2f}, Current: {price:.2f}",
                                  ticker=pos.ticker, strategy="IBS++ v3")
                        from strategies.base import Signal
                        exit_sig = Signal(
                            ticker=pos.ticker,
                            signal_type=SignalType.LONG_EXIT,
                            strategy_name=self.ibs.name,
                            reason=f"Max hold {max_hold} bars",
                        )
                        result = self.portfolio.process_signal(exit_sig, price)
                        if result and hasattr(result, 'success') and result.success:
                            log_event("ORDER",
                                      f"{pos.ticker} — Closed after {max_hold} bars @ {result.fill_price or price:.2f}",
                                      f"P&L: £{result.pnl or 0:.2f}",
                                      ticker=pos.ticker, strategy="IBS++ v3")

                # Trend following positions get checked via their strategy
                # (trailing stops are managed inside the strategy's generate_signal)

            except Exception as e:
                logger.error(f"  Position check error for {key}: {e}")
                log_event("ERROR", f"Position monitor error for {key}",
                          str(e), ticker=pos.ticker if pos else None)

    # ─── Status and shutdown ─────────────────────────────────────────────

    def _show_status(self):
        """Show current account and position status."""
        try:
            acct = self.broker.get_account_info()
            logger.info(f"\nAccount: £{acct.balance:.2f} (P&L: £{acct.unrealised_pnl:.2f})")

            positions = self.broker.get_positions()
            if positions:
                logger.info(f"Open positions ({len(positions)}):")
                for p in positions:
                    logger.info(f"  {p.direction.upper()} {p.ticker} @ {p.entry_price:.2f} (P&L: £{p.unrealised_pnl:.2f})")
            else:
                logger.info("No open positions.")
        except Exception as e:
            logger.warning(f"Could not fetch status: {e}")

    def _shutdown(self, signum=None, frame=None):
        """Handle Ctrl+C gracefully."""
        logger.info("\nShutdown signal received...")
        self.running = False

    def _shutdown_clean(self):
        """Clean shutdown."""
        logger.info("Saving final snapshot...")
        if self.portfolio:
            try:
                self.portfolio.save_snapshot()
            except Exception:
                pass

        log_event("SHUTDOWN", f"Bot stopped ({self.mode.upper()} mode)",
                  f"Ran since startup. Final state: {len(self.portfolio.positions) if self.portfolio else 0} open positions")

        logger.info("Disconnecting broker...")
        if self.broker:
            self.broker.disconnect()

        logger.info("Bot stopped.")


def main():
    parser = argparse.ArgumentParser(description="Continuous Trading Bot Runner")
    parser.add_argument("--mode", choices=["paper", "demo", "live"], default="paper",
                        help="Broker mode (default: paper)")
    args = parser.parse_args()

    config.BROKER_MODE = args.mode
    setup_logging()

    runner = TradingBotRunner(mode=args.mode)
    runner.start()


if __name__ == "__main__":
    main()
