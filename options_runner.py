"""
Options Credit Spread Auto-Trading Bot

Runs the IBS Credit Spread strategy on IG Markets with real money.
Supports shadow mode (log signals, don't trade) and live mode.

Usage:
    python3 options_runner.py                    # shadow mode (default)
    python3 options_runner.py --mode live        # real money
    python3 options_runner.py --mode shadow      # explicit shadow

The bot:
  1. Monitors IBS signals on configured markets (EU close + US close)
  2. When signal fires: finds option EPICs on IG, calculates spread strikes
  3. Sizes the trade via safety controller
  4. In shadow mode: logs what it would do + sends Telegram alert
  5. In live mode: places 2-leg order on IG + sends Telegram alert
  6. Monitors open spreads for expiry / early exit
  7. Heartbeat every 2 hours so you know it's alive
"""
import argparse
import json
import logging
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, date
from typing import Optional
from zoneinfo import ZoneInfo

import config
from data.provider import DataProvider
from strategies.ibs_credit_spreads import (
    generate_signal, CreditSpreadSignal, OptionPosition,
)
from broker.ig import IGBroker
from broker.paper import PaperBroker
from portfolio.risk import calc_option_spread_size
from safety_controller import SafetyController, SafetyLimits
from notifications import notifier
from data.trade_db import (
    log_event, log_trade, log_shadow_trade,
    upsert_option_position, close_option_position,
    get_open_option_positions, get_order_actions_by_statuses,
    create_order_action, update_order_action,
    load_strategy_state, save_strategy_state, log_control_action,
    get_active_strategy_parameter_set,
)
from utils.logger import setup_logging

logger = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")

# ─── Schedule (UK times) ──────────────────────────────────────────────────────

SCHEDULE = {
    "eu_close": {"hour": 17, "minute": 5},
    "us_close": {"hour": 21, "minute": 15},
    "daily_snapshot": {"hour": 22, "minute": 0},
}

# Which tickers to check at each close
EU_TICKERS = ["EWU", "EWG", "EWJ"]
US_TICKERS = ["SPY", "QQQ", "GLD"]

POSITION_CHECK_INTERVAL = 300   # 5 min
HEARTBEAT_INTERVAL = 7200       # 2 hours
ORDER_ACTION_MAX_ATTEMPTS = 3


class OptionsBot:
    """
    Auto-trading bot for IBS Credit Spreads on IG.
    Shadow mode by default — toggle to live via --mode live.
    """

    def __init__(self, mode: str = "shadow"):
        self.mode = mode  # "shadow" or "live"
        self.is_shadow = (mode == "shadow")
        self.running = False
        self.paused = False

        # Core components
        self.broker = None
        self.data = DataProvider(lookback_days=500)
        self.safety = SafetyController(
            initial_equity=5000,
            limits=SafetyLimits(**config.OPTIONS_SAFETY),
        )

        # Track open option positions in memory (also persisted to DB)
        self.open_spreads: dict[str, dict] = {}  # spread_id → position dict

        # Dedup: don't fire same signal twice in one day
        self._today_signals: set = set()
        self._last_eu_check = None
        self._last_us_check = None
        self._last_snapshot = None
        self._last_heartbeat = None
        self._last_position_check = None
        self._pause_announced = False
        self._control_lock = threading.RLock()

        # Operator risk overrides (kill/throttle/cooldowns).
        self.kill_switch_active = False
        self.kill_switch_reason = ""
        self.risk_throttle_pct = 1.0
        self.market_cooldowns: dict[str, datetime] = {}
        self.strategy_params: dict = dict(config.IBS_CREDIT_SPREAD_PARAMS)
        self.active_param_set_id: Optional[str] = None
        self.active_param_set_status: str = "default"

    # ─── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, once: bool = False, install_signal_handlers: bool = True) -> bool:
        """Start the bot."""
        self._load_strategy_parameters()
        logger.info("=" * 60)
        logger.info(f"OPTIONS BOT STARTING — {datetime.now(UK).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info(f"Mode: {'SHADOW (no real trades)' if self.is_shadow else 'LIVE (real money!)'}")
        logger.info(f"Markets: {', '.join(config.LIVE_TRADING_TICKERS)}")
        logger.info(f"Strategy: IBS Credit Spreads")
        if self.active_param_set_id:
            logger.info(
                f"Strategy params: set={self.active_param_set_id[:8]} status={self.active_param_set_status}"
            )
        else:
            logger.info("Strategy params: config defaults")
        logger.info(f"Safety: {config.OPTIONS_SAFETY}")
        logger.info("=" * 60)

        log_event("STARTUP",
                  f"Options bot started in {self.mode.upper()} mode",
                  f"Markets: {', '.join(config.LIVE_TRADING_TICKERS)}")

        # Connect to broker
        if self.mode == "live":
            self.broker = IGBroker(is_demo=False)
        else:
            # Shadow mode still connects to IG (read-only) for option pricing
            self.broker = IGBroker(is_demo=(config.IG_ACC_TYPE == "DEMO"))

        if not self.broker.connect():
            logger.error("Failed to connect to IG. Aborting.")
            notifier.error("Bot startup failed — cannot connect to IG")
            return False

        # Get initial equity
        acct = self.broker.get_account_info()
        if acct.equity > 0:
            self.safety.equity = acct.equity
            logger.info(f"Account equity: £{acct.equity:,.2f}")

        # Load open positions from DB
        self._load_open_positions()
        self._load_control_state()
        self._startup_recover()

        # Handle signals only in main thread.
        if install_signal_handlers and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._shutdown)
            signal.signal(signal.SIGTERM, self._shutdown)

        self.running = True

        notifier.send(
            f"Bot started — {self.mode.upper()} mode\n"
            f"Equity: £{self.safety.equity:,.0f}\n"
            f"Markets: {', '.join(config.LIVE_TRADING_TICKERS)}",
            icon="🚀",
        )

        # Initial scan
        logger.info("\nRunning initial signal scan...")
        self._run_all_signals()

        if once:
            logger.info("One-shot mode complete. Shutting down.")
            self.running = False
            self._shutdown_clean()
            return True

        logger.info("\nBot is running. Press Ctrl+C to stop.\n")

        # Main loop
        while self.running:
            try:
                self._tick()
                time.sleep(30)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                notifier.error(f"Main loop error: {e}")
                time.sleep(60)

        self._shutdown_clean()
        return True

    def _tick(self):
        """Called every 30 seconds."""
        with self._control_lock:
            now = datetime.now(UK)
            today = now.date()
            self._clear_expired_cooldowns()

            # Reset daily signal dedup at midnight
            if self._last_eu_check and self._last_eu_check != today:
                self._today_signals.clear()

            # Heartbeat
            if self._last_heartbeat is None or (now - self._last_heartbeat).seconds >= HEARTBEAT_INTERVAL:
                self._heartbeat(now)
                self._last_heartbeat = now

            # Pause gate
            if self.paused:
                if not self._pause_announced:
                    logger.info("Bot paused — scheduled scans temporarily disabled")
                    log_event("POSITION", "Options bot paused", "No new scans while paused")
                    self._pause_announced = True
                return

            if self._pause_announced:
                logger.info("Bot resumed — scheduled scans enabled")
                log_event("POSITION", "Options bot resumed", "Scheduled scans resumed")
                self._pause_announced = False

            # EU close (5:05pm UK)
            if (now.hour == SCHEDULE["eu_close"]["hour"]
                and now.minute >= SCHEDULE["eu_close"]["minute"]
                and self._last_eu_check != today):
                logger.info("\n── EUROPEAN CLOSE ──")
                log_event("SCAN", "EU close scan", f"Checking {', '.join(EU_TICKERS)}")
                self._run_signals(EU_TICKERS)
                self._last_eu_check = today

            # US close (9:15pm UK)
            if (now.hour == SCHEDULE["us_close"]["hour"]
                and now.minute >= SCHEDULE["us_close"]["minute"]
                and self._last_us_check != today):
                logger.info("\n── US CLOSE ──")
                log_event("SCAN", "US close scan", f"Checking {', '.join(US_TICKERS)}")
                self._run_signals(US_TICKERS)
                self._last_us_check = today

            # Position monitoring (every 5 min during market hours)
            if (8 <= now.hour <= 22
                and (self._last_position_check is None
                     or (now - self._last_position_check).seconds >= POSITION_CHECK_INTERVAL)):
                self._monitor_positions()
                self._last_position_check = now

            # Daily snapshot (10pm)
            if (now.hour == SCHEDULE["daily_snapshot"]["hour"]
                and self._last_snapshot != today):
                self._daily_snapshot()
                self._last_snapshot = today

    # ─── Signal scanning ───────────────────────────────────────────────────

    def _run_all_signals(self):
        """Scan all configured markets."""
        tickers = [t for t in config.LIVE_TRADING_TICKERS
                   if t in EU_TICKERS + US_TICKERS]
        if not tickers:
            tickers = config.LIVE_TRADING_TICKERS
        self._run_signals(tickers)

    def _run_signals(self, tickers: list[str]):
        """Run IBS credit spread strategy on given tickers."""
        vix_df = self.data.get_vix()
        vix = float(vix_df["Close"].iloc[-1]) if not vix_df.empty else 20.0

        for ticker in tickers:
            if ticker not in config.LIVE_TRADING_TICKERS:
                continue

            try:
                df = self.data.get_daily_bars(ticker)
                if df.empty or len(df) < 210:  # Need 200 EMA + buffer
                    logger.warning(f"  {ticker}: insufficient data")
                    continue

                # Prepare bar data for strategy
                bar = {
                    "open": float(df["Open"].iloc[-1]),
                    "high": float(df["High"].iloc[-1]),
                    "low": float(df["Low"].iloc[-1]),
                    "close": float(df["Close"].iloc[-1]),
                    "date": str(df.index[-1].date()),
                }
                prev_bars = [
                    {"close": float(df["Close"].iloc[i])}
                    for i in range(-20, -1)
                ]

                # 200 EMA
                ema200 = float(df["Close"].ewm(span=200, adjust=False).mean().iloc[-1])

                # Check if we already have an open position for this ticker
                existing = self._get_open_spread(ticker)

                # Generate signal
                sig = generate_signal(
                    bar=bar,
                    prev_bars=prev_bars,
                    position=existing,
                    params=self.strategy_params,
                    vix=vix,
                    ema200=ema200,
                )

                self._handle_signal(ticker, sig, bar["close"], vix)

            except Exception as e:
                logger.error(f"  {ticker} error: {e}", exc_info=True)
                log_event("ERROR", f"{ticker} — Signal error: {e}",
                          ticker=ticker, strategy="IBS Credit Spreads")

    def _handle_signal(self, ticker: str, sig: CreditSpreadSignal,
                       current_price: float, vix: float):
        """Process a strategy signal — open, close, or skip."""

        if sig.action == "skip":
            logger.debug(f"  {ticker}: {sig.reason}")
            return

        if sig.action == "hold":
            logger.debug(f"  {ticker}: {sig.reason}")
            return

        # ─── Close signal ──────────────────────────────────────────────
        if sig.action == "close":
            spread = self._get_open_spread_dict(ticker)
            if not spread:
                logger.warning(f"  {ticker}: close signal but no open spread")
                return

            logger.info(f"  {ticker}: CLOSE — {sig.reason}")
            self._close_spread(spread, sig.reason)
            return

        # ─── Entry signal ─────────────────────────────────────────────
        if sig.action in ("open_put_spread", "open_call_spread"):
            # Dedup: don't enter same ticker twice in one day
            dedup_key = f"{ticker}:{date.today()}"
            if dedup_key in self._today_signals:
                logger.debug(f"  {ticker}: already signalled today, skipping")
                return
            self._today_signals.add(dedup_key)

            logger.info(f"  {ticker}: {sig.action.upper()} — {sig.reason}")
            logger.info(f"    Short: {sig.short_strike}, Long: {sig.long_strike}")

            self._enter_spread(ticker, sig, current_price)
            return

    # ─── Trade execution ───────────────────────────────────────────────────

    def _enter_spread(self, ticker: str, sig: CreditSpreadSignal,
                      current_price: float):
        """Enter a credit spread — or log it in shadow mode."""

        if self.kill_switch_active:
            detail = self.kill_switch_reason or "Kill switch active"
            logger.warning(f"  {ticker}: blocked by kill switch — {detail}")
            log_event(
                "REJECTION",
                f"{ticker} — Entry blocked by kill switch",
                detail,
                ticker=ticker,
                strategy="IBS Credit Spreads",
            )
            notifier.trade_rejected(ticker, f"Kill switch active: {detail}")
            return

        remaining = self._cooldown_remaining(ticker)
        if remaining:
            mins_left = max(1, int(remaining.total_seconds() // 60))
            reason = f"Market cooldown active ({mins_left}m remaining)"
            logger.info(f"  {ticker}: {reason}")
            log_event("REJECTION", f"{ticker} — Entry blocked by cooldown", reason,
                      ticker=ticker, strategy="IBS Credit Spreads")
            notifier.trade_rejected(ticker, reason)
            return

        spread_width = sig.short_strike - sig.long_strike
        if spread_width <= 0:
            logger.warning(f"  {ticker}: invalid spread width {spread_width}")
            return

        # Estimate premium (from backtester's BS model or use a default)
        # In live mode we'd get real quotes; for now use ~30% of width as estimate
        estimated_premium = spread_width * 0.30

        max_loss_per_contract = spread_width - estimated_premium

        # Size the trade
        size_result = calc_option_spread_size(
            equity=self.safety.equity,
            spread_width=spread_width,
            premium=estimated_premium,
            max_risk_pct=config.OPTIONS_SAFETY["max_risk_per_trade_pct"],
            kelly_fraction=self.strategy_params.get("kelly_fraction", 0.25),
        )

        if size_result.stake_per_point <= 0:
            logger.info(f"  {ticker}: sizing returned 0 — {size_result.notes}")
            return

        num_contracts = int(size_result.stake_per_point)
        if self.risk_throttle_pct < 1.0:
            throttled = max(1, int(num_contracts * self.risk_throttle_pct))
            if throttled < num_contracts:
                logger.info(
                    f"  {ticker}: risk throttle {self.risk_throttle_pct:.2f} applied "
                    f"({num_contracts} -> {throttled} contracts)"
                )
                num_contracts = throttled
        total_risk = num_contracts * max_loss_per_contract

        # Safety check
        viable, reason = self.safety.check_order_viability(
            proposed_risk=total_risk,
            proposed_size=num_contracts,
            premium_pct=(estimated_premium / spread_width * 100) if spread_width > 0 else 0,
        )

        if not viable:
            logger.info(f"  {ticker}: BLOCKED by safety — {reason}")
            log_event("REJECTION", f"{ticker} — Safety blocked: {reason}",
                      ticker=ticker, strategy="IBS Credit Spreads")
            notifier.trade_rejected(ticker, reason)
            return

        # ─── Shadow mode: log but don't execute ──────────────────────
        if self.is_shadow:
            logger.info(
                f"  [SHADOW] {ticker}: Would place {sig.action} — "
                f"{num_contracts} contracts, risk=£{total_risk:.0f}"
            )
            log_shadow_trade(
                ticker=ticker,
                strategy="IBS Credit Spreads",
                action=sig.action,
                short_strike=sig.short_strike,
                long_strike=sig.long_strike,
                spread_width=spread_width,
                estimated_premium=estimated_premium,
                max_loss=max_loss_per_contract,
                size=num_contracts,
                reason=sig.reason,
            )
            notifier.shadow_trade(
                ticker, sig.action, sig.short_strike, sig.long_strike, sig.reason,
            )
            log_event("SIGNAL",
                      f"[SHADOW] {ticker} — {sig.action}",
                      f"Short={sig.short_strike}, Long={sig.long_strike}, "
                      f"{num_contracts} contracts, risk=£{total_risk:.0f}. "
                      f"Reason: {sig.reason}",
                      ticker=ticker, strategy="IBS Credit Spreads")
            return

        # ─── Live mode: find option EPICs and place orders ───────────
        logger.info(f"  {ticker}: PLACING LIVE ORDER — {num_contracts} contracts")
        spread_id = f"{ticker}:{uuid.uuid4().hex[:8]}"
        action_id = uuid.uuid4().hex
        correlation_id = self._new_correlation_id("open_spread", ticker)
        max_attempts = max(1, int(config.OPTIONS_SAFETY.get("order_max_attempts", ORDER_ACTION_MAX_ATTEMPTS)))
        option_type = "PUT" if sig.action == "open_put_spread" else "CALL"
        option_side_text = "put" if option_type == "PUT" else "call"

        create_order_action(
            action_id=action_id,
            correlation_id=correlation_id,
            action_type="open_spread",
            ticker=ticker,
            spread_id=spread_id,
            max_attempts=max_attempts,
            request_payload=json.dumps({
                "ticker": ticker,
                "signal_action": sig.action,
                "short_target": sig.short_strike,
                "long_target": sig.long_strike,
                "size": num_contracts,
                "reason": sig.reason,
            }),
        )

        short_option = None
        long_option = None
        result = None
        last_error = "Unknown order failure"
        last_code = "UNKNOWN_EXECUTION_ERROR"

        for attempt in range(1, max_attempts + 1):
            update_order_action(
                action_id=action_id,
                status="running",
                attempt=attempt,
                recoverable=False,
                error_code="",
                error_message="",
            )
            attempt_correlation = f"{correlation_id}:a{attempt}"
            try:
                option_cfg = config.OPTION_EPIC_PATTERNS.get(ticker)
                if not option_cfg:
                    last_error = "No option EPIC pattern configured"
                    last_code, recoverable = self._classify_order_error(last_error, "NO_OPTION_CONFIG")
                else:
                    search_term = f"{option_cfg['search']} {option_side_text}"
                    options = self.broker.search_option_markets(search_term)
                    if not options:
                        last_error = f"No options found on IG for '{search_term}'"
                        last_code, recoverable = self._classify_order_error(last_error, "OPTIONS_NOT_FOUND")
                    else:
                        short_option = self._find_closest_option(options, sig.short_strike, option_type)
                        long_option = self._find_closest_option(options, sig.long_strike, option_type)
                        if not short_option or not long_option:
                            last_error = "Couldn't find matching options"
                            last_code, recoverable = self._classify_order_error(last_error, "OPTION_MATCH_FAILED")
                        else:
                            logger.info(f"    Short leg: {short_option.epic} (strike={short_option.strike})")
                            logger.info(f"    Long leg: {long_option.epic} (strike={long_option.strike})")

                            # Pre-trade guardrails from broker dealing rules.
                            short_check = self.broker.validate_option_leg(short_option.epic, float(num_contracts))
                            if not short_check.get("ok"):
                                hint = str(short_check.get("code", "LEG_VALIDATION_FAILED"))
                                last_error = str(short_check.get("message", "Short leg failed validation"))
                                last_code, recoverable = self._classify_order_error(last_error, hint)
                            else:
                                long_check = self.broker.validate_option_leg(long_option.epic, float(num_contracts))
                                if not long_check.get("ok"):
                                    hint = str(long_check.get("code", "LEG_VALIDATION_FAILED"))
                                    last_error = str(long_check.get("message", "Long leg failed validation"))
                                    last_code, recoverable = self._classify_order_error(last_error, hint)
                                else:
                                    result = self.broker.place_option_spread(
                                        short_epic=short_option.epic,
                                        long_epic=long_option.epic,
                                        size=float(num_contracts),
                                        ticker=ticker,
                                        strategy="IBS Credit Spreads",
                                        correlation_id=attempt_correlation,
                                    )
                                    if result.success:
                                        update_order_action(
                                            action_id=action_id,
                                            status="completed",
                                            attempt=attempt,
                                            recoverable=False,
                                            error_code="",
                                            error_message="",
                                            result_payload=json.dumps({
                                                "short_deal_id": result.short_deal_id,
                                                "long_deal_id": result.long_deal_id,
                                                "short_fill_price": result.short_fill_price,
                                                "long_fill_price": result.long_fill_price,
                                                "net_premium": result.net_premium,
                                            }),
                                        )
                                        break
                                    last_error = result.message or "Spread order failed"
                                    last_code, recoverable = self._classify_order_error(last_error)
            except Exception as exc:
                last_error = str(exc)
                last_code, recoverable = self._classify_order_error(last_error)

            if result and result.success:
                break

            if recoverable and attempt < max_attempts:
                update_order_action(
                    action_id=action_id,
                    status="retrying",
                    attempt=attempt,
                    recoverable=True,
                    error_code=last_code,
                    error_message=last_error,
                )
                wait_s = self._retry_backoff_seconds(attempt)
                logger.warning(
                    f"  {ticker}: open attempt {attempt}/{max_attempts} failed "
                    f"({last_code}) — retrying in {wait_s:.0f}s: {last_error}"
                )
                time.sleep(wait_s)
                continue

            update_order_action(
                action_id=action_id,
                status="failed",
                attempt=attempt,
                recoverable=recoverable,
                error_code=last_code,
                error_message=last_error,
            )
            result = None
            break

        if not result or not result.success:
            logger.error(f"  {ticker}: spread order failed — {last_code}: {last_error}")
            log_event(
                "REJECTION",
                f"{ticker} — Order failed ({last_code})",
                last_error,
                ticker=ticker,
                strategy="IBS Credit Spreads",
            )
            notifier.error(f"{ticker}: order failed ({last_code}) — {last_error}")
            return

        # Record the position
        actual_premium = result.net_premium
        actual_max_loss = spread_width - actual_premium if actual_premium > 0 else max_loss_per_contract

        self.open_spreads[spread_id] = {
            "spread_id": spread_id,
            "ticker": ticker,
            "trade_type": sig.action.replace("open_", ""),
            "short_deal_id": result.short_deal_id,
            "long_deal_id": result.long_deal_id,
            "short_strike": short_option.strike,
            "long_strike": long_option.strike,
            "short_epic": short_option.epic,
            "long_epic": long_option.epic,
            "spread_width": spread_width,
            "premium_collected": actual_premium,
            "max_loss": actual_max_loss,
            "size": num_contracts,
            "entry_date": datetime.now().isoformat(),
            "bars_held": 0,
        }

        # Persist to DB
        upsert_option_position(
            spread_id=spread_id, ticker=ticker, strategy="IBS Credit Spreads",
            trade_type=sig.action.replace("open_", ""),
            short_deal_id=result.short_deal_id, long_deal_id=result.long_deal_id,
            short_strike=short_option.strike, long_strike=long_option.strike,
            short_epic=short_option.epic, long_epic=long_option.epic,
            spread_width=spread_width, premium_collected=actual_premium,
            max_loss=actual_max_loss, size=num_contracts,
        )

        # Log
        log_trade(
            ticker=ticker, strategy="IBS Credit Spreads",
            direction="SELL", action="OPEN", size=num_contracts,
            price=current_price,
            deal_id=result.short_deal_id,
            notes=(
                f"Credit spread: short={short_option.strike}, long={long_option.strike}, "
                f"premium={actual_premium:.1f}, max_loss={actual_max_loss:.1f}, "
                f"contracts={num_contracts}"
            ),
        )
        log_event("ORDER",
                  f"{ticker} — Credit spread opened",
                  f"Short={short_option.strike}, Long={long_option.strike}, "
                  f"{num_contracts} contracts, premium={actual_premium:.1f}pts",
                  ticker=ticker, strategy="IBS Credit Spreads")

        notifier.trade_entered(
            ticker, short_option.strike, long_option.strike,
            num_contracts, actual_premium, actual_max_loss * num_contracts,
        )

        logger.info(f"  {ticker}: SPREAD OPENED — {spread_id}")

    def _close_spread(self, spread: dict, reason: str) -> bool:
        """Close an open spread."""
        ticker = spread["ticker"]
        spread_id = spread["spread_id"]

        if self.is_shadow:
            logger.info(f"  [SHADOW] {ticker}: Would close spread — {reason}")
            log_shadow_trade(
                ticker=ticker, strategy="IBS Credit Spreads",
                action="close", reason=reason,
                short_strike=spread.get("short_strike", 0),
                long_strike=spread.get("long_strike", 0),
            )
            # In shadow mode, remove from tracking (simulate the close)
            self.open_spreads.pop(spread_id, None)
            return True

        action_id = uuid.uuid4().hex
        correlation_id = self._new_correlation_id("close_spread", ticker)
        max_attempts = max(1, int(config.OPTIONS_SAFETY.get("order_max_attempts", ORDER_ACTION_MAX_ATTEMPTS)))
        create_order_action(
            action_id=action_id,
            correlation_id=correlation_id,
            action_type="close_spread",
            ticker=ticker,
            spread_id=spread_id,
            max_attempts=max_attempts,
            request_payload=json.dumps({
                "spread_id": spread_id,
                "ticker": ticker,
                "short_deal_id": spread.get("short_deal_id"),
                "long_deal_id": spread.get("long_deal_id"),
                "size": spread.get("size"),
                "reason": reason,
            }),
        )

        result = None
        last_error = "Unknown close failure"
        last_code = "UNKNOWN_EXECUTION_ERROR"

        for attempt in range(1, max_attempts + 1):
            update_order_action(
                action_id=action_id,
                status="running",
                attempt=attempt,
                recoverable=False,
                error_code="",
                error_message="",
            )
            attempt_correlation = f"{correlation_id}:a{attempt}"

            try:
                result = self.broker.close_option_spread(
                    short_deal_id=spread["short_deal_id"],
                    long_deal_id=spread["long_deal_id"],
                    size=float(spread["size"]),
                    correlation_id=attempt_correlation,
                )
                if result.success:
                    update_order_action(
                        action_id=action_id,
                        status="completed",
                        attempt=attempt,
                        recoverable=False,
                        error_code="",
                        error_message="",
                        result_payload=json.dumps({
                            "short_deal_id": spread["short_deal_id"],
                            "long_deal_id": spread["long_deal_id"],
                            "short_fill_price": result.short_fill_price,
                            "long_fill_price": result.long_fill_price,
                            "net_close_cost": result.net_premium,
                        }),
                    )
                    break
                last_error = result.message or "Spread close failed"
                last_code, recoverable = self._classify_order_error(last_error)
            except Exception as exc:
                last_error = str(exc)
                last_code, recoverable = self._classify_order_error(last_error)

            if recoverable and attempt < max_attempts:
                update_order_action(
                    action_id=action_id,
                    status="retrying",
                    attempt=attempt,
                    recoverable=True,
                    error_code=last_code,
                    error_message=last_error,
                )
                wait_s = self._retry_backoff_seconds(attempt)
                logger.warning(
                    f"  {ticker}: close attempt {attempt}/{max_attempts} failed "
                    f"({last_code}) — retrying in {wait_s:.0f}s: {last_error}"
                )
                time.sleep(wait_s)
                continue

            update_order_action(
                action_id=action_id,
                status="failed",
                attempt=attempt,
                recoverable=recoverable,
                error_code=last_code,
                error_message=last_error,
            )
            result = None
            break

        if not result or not result.success:
            logger.error(f"  {ticker}: close failed — {last_code}: {last_error}")
            log_event("ERROR", f"{ticker} — Spread close failed ({last_code})",
                      last_error, ticker=ticker, strategy="IBS Credit Spreads")
            notifier.error(f"{ticker}: close failed ({last_code}) — {last_error}")
            return False

        # Calculate P&L
        entry_premium = spread.get("premium_collected", 0)
        exit_cost = abs(result.net_premium) if result.net_premium else 0
        pnl = (entry_premium - exit_cost) * spread["size"]

        # Record
        close_option_position(spread_id, exit_pnl=pnl, exit_reason=reason)
        self.safety.record_closed_trade(pnl)
        self.open_spreads.pop(spread_id, None)

        log_trade(
            ticker=ticker, strategy="IBS Credit Spreads",
            direction="BUY", action="CLOSE", size=spread["size"],
            deal_id=spread["short_deal_id"], pnl=pnl,
            notes=f"Closed: {reason}, P&L=£{pnl:.2f}",
        )
        log_event("ORDER", f"{ticker} — Spread closed (£{pnl:+.2f})",
                  f"Reason: {reason}", ticker=ticker, strategy="IBS Credit Spreads")

        notifier.trade_closed(ticker, pnl, reason)
        logger.info(f"  {ticker}: SPREAD CLOSED — P&L=£{pnl:+.2f}")
        return True

    # ─── Position monitoring ───────────────────────────────────────────────

    def _monitor_positions(self):
        """Check open spreads for expiry / exit conditions."""
        if not self.open_spreads:
            return

        for spread_id, spread in list(self.open_spreads.items()):
            spread["bars_held"] = spread.get("bars_held", 0) + 1
            max_hold = self.strategy_params.get("max_hold_bars", 10)

            if spread["bars_held"] >= max_hold:
                logger.info(f"  {spread['ticker']}: max hold reached ({max_hold} bars)")
                self._close_spread(spread, f"Max hold {max_hold} bars (expiry)")

    def _get_open_spread(self, ticker: str) -> Optional[OptionPosition]:
        """Get OptionPosition for strategy signal generation."""
        for s in self.open_spreads.values():
            if s["ticker"] == ticker:
                return OptionPosition(
                    trade_type=s.get("trade_type", "put_spread"),
                    entry_date=s.get("entry_date", ""),
                    entry_price=0,
                    short_strike=s.get("short_strike", 0),
                    long_strike=s.get("long_strike", 0),
                    premium_collected=s.get("premium_collected", 0),
                    spread_width=s.get("spread_width", 0),
                    max_loss=s.get("max_loss", 0),
                    contracts=int(s.get("size", 1)),
                    bars_held=s.get("bars_held", 0),
                    days_to_expiry=self.strategy_params.get("expiry_days", 10),
                )
        return None

    def _load_strategy_parameters(self):
        """Load active parameter set for current mode, falling back to config defaults."""
        self.strategy_params = dict(config.IBS_CREDIT_SPREAD_PARAMS)
        self.active_param_set_id = None
        self.active_param_set_status = "default"

        preferred_status = "live" if self.mode == "live" else "shadow"
        row = get_active_strategy_parameter_set("ibs_credit_spreads", status=preferred_status)
        if not row and self.mode != "live":
            # Allow shadow mode to use a staged set if no shadow default exists.
            row = get_active_strategy_parameter_set("ibs_credit_spreads", status="staged_live")
        if not row:
            return

        try:
            payload = json.loads(row.get("parameters_payload") or "{}")
        except json.JSONDecodeError:
            logger.warning("Invalid strategy parameter set payload for %s; using defaults", row.get("id"))
            return

        if not isinstance(payload, dict):
            logger.warning("Parameter set payload is not an object for %s; using defaults", row.get("id"))
            return

        self.strategy_params.update(payload)
        self.active_param_set_id = str(row.get("id") or "")
        self.active_param_set_status = str(row.get("status") or "unknown")

    def _get_open_spread_dict(self, ticker: str) -> Optional[dict]:
        """Get raw spread dict for a ticker."""
        for s in self.open_spreads.values():
            if s["ticker"] == ticker:
                return s
        return None

    def _load_open_positions(self):
        """Load open option positions from DB on startup."""
        db_positions = get_open_option_positions()
        for pos in db_positions:
            self.open_spreads[pos["spread_id"]] = pos
        if db_positions:
            logger.info(f"Loaded {len(db_positions)} open option positions from DB")
            self.safety.update_state(self.safety.equity, list(self.open_spreads.values()))

    def _persist_control_state(self):
        payload = {
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "risk_throttle_pct": self.risk_throttle_pct,
            "market_cooldowns": {
                ticker: expires.isoformat()
                for ticker, expires in self.market_cooldowns.items()
            },
        }
        save_strategy_state("risk_overrides", json.dumps(payload))

    def _load_control_state(self):
        raw = load_strategy_state("risk_overrides")
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid risk_overrides state payload; ignoring")
            return

        self.kill_switch_active = bool(payload.get("kill_switch_active", False))
        self.kill_switch_reason = str(payload.get("kill_switch_reason", "") or "")
        throttle = float(payload.get("risk_throttle_pct", 1.0) or 1.0)
        self.risk_throttle_pct = min(1.0, max(0.1, throttle))
        self.market_cooldowns = {}
        now = datetime.now(UK)
        for ticker, iso_value in (payload.get("market_cooldowns") or {}).items():
            try:
                expires = datetime.fromisoformat(str(iso_value))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UK)
                if expires > now:
                    self.market_cooldowns[str(ticker).upper()] = expires
            except Exception:
                continue

    def _clear_expired_cooldowns(self):
        now = datetime.now(UK)
        expired = [ticker for ticker, until in self.market_cooldowns.items() if until <= now]
        for ticker in expired:
            self.market_cooldowns.pop(ticker, None)
        if expired:
            self._persist_control_state()

    def _cooldown_remaining(self, ticker: str) -> Optional[timedelta]:
        self._clear_expired_cooldowns()
        until = self.market_cooldowns.get(str(ticker).upper())
        if not until:
            return None
        now = datetime.now(UK)
        remaining = until - now
        return remaining if remaining.total_seconds() > 0 else None

    def _startup_recover(self):
        """
        Resolve stale pending order actions left by previous crash/termination.
        """
        pending = get_order_actions_by_statuses(["queued", "running", "retrying"], limit=500)
        if not pending:
            return

        db_positions = {p["spread_id"]: p for p in get_open_option_positions()}
        broker_deal_ids = set()
        try:
            for broker_pos in self.broker.get_positions():
                deal_id = str(getattr(broker_pos, "deal_id", "") or "").strip()
                if deal_id:
                    broker_deal_ids.add(deal_id)
        except Exception as exc:
            logger.error(f"Startup recovery: broker positions fetch failed: {exc}")
            log_event("ERROR", "Startup recovery failed to fetch broker positions", str(exc),
                      strategy="IBS Credit Spreads")

        completed = 0
        failed = 0

        for action in pending:
            action_id = str(action.get("id"))
            action_type = str(action.get("action_type", ""))
            spread_id = str(action.get("spread_id", "") or "")
            attempt = int(action.get("attempt", 0) or 0)
            max_attempts = int(action.get("max_attempts", 1) or 1)
            pos = db_positions.get(spread_id)

            if action_type == "open_spread":
                if pos:
                    short_id = str(pos.get("short_deal_id", "") or "")
                    long_id = str(pos.get("long_deal_id", "") or "")
                    short_ok = (not short_id) or (short_id in broker_deal_ids)
                    long_ok = (not long_id) or (long_id in broker_deal_ids)
                    if short_ok and long_ok:
                        update_order_action(
                            action_id=action_id,
                            status="completed",
                            attempt=max(attempt, 1),
                            recoverable=False,
                            error_code="",
                            error_message="",
                            result_payload=json.dumps({
                                "startup_recovered": True,
                                "reason": "Open spread exists in DB and broker position set is consistent",
                            }),
                        )
                        completed += 1
                        continue
                update_order_action(
                    action_id=action_id,
                    status="failed",
                    attempt=max(attempt, max_attempts),
                    recoverable=True,
                    error_code="STARTUP_INCOMPLETE_OPEN",
                    error_message="Pending open_spread not confirmed during startup recovery",
                )
                failed += 1
                continue

            if action_type == "close_spread":
                if not pos:
                    update_order_action(
                        action_id=action_id,
                        status="completed",
                        attempt=max(attempt, 1),
                        recoverable=False,
                        error_code="",
                        error_message="",
                        result_payload=json.dumps({
                            "startup_recovered": True,
                            "reason": "Spread already absent from DB after close action",
                        }),
                    )
                    completed += 1
                    continue
                update_order_action(
                    action_id=action_id,
                    status="failed",
                    attempt=max(attempt, max_attempts),
                    recoverable=True,
                    error_code="STARTUP_INCOMPLETE_CLOSE",
                    error_message="Pending close_spread still present in DB during startup recovery",
                )
                failed += 1
                continue

            update_order_action(
                action_id=action_id,
                status="aborted",
                attempt=max(attempt, 1),
                recoverable=False,
                error_code="UNKNOWN_ACTION_TYPE",
                error_message=f"Unsupported action_type={action_type}",
            )
            failed += 1

        summary = f"pending={len(pending)} recovered={completed} unresolved={failed}"
        logger.info(f"Startup recovery complete ({summary})")
        log_control_action(
            action="startup_recovery",
            value=summary,
            reason="Recovered pending order action states after startup",
            actor="system",
        )
        if failed:
            log_event("ERROR", "Startup recovery left unresolved actions", summary, strategy="IBS Credit Spreads")
        else:
            log_event("POSITION", "Startup recovery complete", summary, strategy="IBS Credit Spreads")

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _find_closest_option(self, options: list, target_strike: float,
                             option_type: str):
        """Find the option closest to target strike."""
        from broker.base import OptionMarket
        matching = [o for o in options if o.option_type == option_type and o.strike > 0]
        if not matching:
            return None
        return min(matching, key=lambda o: abs(o.strike - target_strike))

    def _new_correlation_id(self, action: str, ticker: str) -> str:
        stamp = datetime.now(UK).strftime("%Y%m%d%H%M%S")
        return f"{action}:{ticker}:{stamp}:{uuid.uuid4().hex[:8]}"

    def _classify_order_error(self, message: str, code_hint: str = "") -> tuple[str, bool]:
        """
        Return (error_code, recoverable) for deterministic retry behavior.
        """
        code = (code_hint or "").strip().upper()
        msg = (message or "").strip()
        lower = msg.lower()

        if not code:
            if "short leg reversed" in lower:
                code = "LEG2_FAILED_REVERSED"
            elif "timeout" in lower or "timed out" in lower or "temporar" in lower:
                code = "TRANSIENT_TIMEOUT"
            elif "connection" in lower or "network" in lower:
                code = "NETWORK_ERROR"
            elif "http 5" in lower:
                code = "BROKER_5XX"
            elif "market_not_tradeable" in lower or "marketstatus" in lower:
                code = "MARKET_NOT_TRADEABLE"
            elif "size_below_min" in lower or "below mindealsize" in lower:
                code = "SIZE_BELOW_MIN"
            elif "no market info" in lower:
                code = "NO_MARKET_INFO"
            elif "rejected" in lower or "403" in lower:
                code = "BROKER_REJECTED"
            elif "no options found" in lower:
                code = "OPTIONS_NOT_FOUND"
            elif "couldn't find matching options" in lower:
                code = "OPTION_MATCH_FAILED"
            elif "short closed but long close failed" in lower:
                code = "PARTIAL_CLOSE"
            else:
                code = "UNKNOWN_EXECUTION_ERROR"

        recoverable_codes = {
            "LEG2_FAILED_REVERSED",
            "TRANSIENT_TIMEOUT",
            "NETWORK_ERROR",
            "BROKER_5XX",
            "NO_MARKET_INFO",
        }
        return code, code in recoverable_codes

    def _retry_backoff_seconds(self, attempt: int) -> float:
        # Deterministic, short backoff for local operator workflow.
        return float(min(10, attempt * 2))

    def _heartbeat(self, now):
        """Send periodic status update."""
        acct = self.broker.get_account_info()
        equity = acct.equity if acct.equity > 0 else self.safety.equity

        status = self.safety.get_status()
        logger.info(
            f"[heartbeat] {now.strftime('%H:%M')} UK | "
            f"{'SHADOW' if self.is_shadow else 'LIVE'} | "
            f"{len(self.open_spreads)} spreads | "
            f"equity=£{equity:,.0f} | "
            f"heat={status['heat_pct']:.0f}%"
        )
        log_event("HEARTBEAT",
                  f"Options bot alive — {len(self.open_spreads)} spreads",
                  f"Equity: £{equity:,.0f}, Heat: {status['heat_pct']:.0f}%")

        notifier.heartbeat(
            len(self.open_spreads), equity,
            status["daily_pnl"], self.mode,
        )

    def _daily_snapshot(self):
        """End of day snapshot."""
        acct = self.broker.get_account_info()
        from data.trade_db import save_daily_snapshot
        save_daily_snapshot(
            balance=acct.balance, equity=acct.equity,
            unrealised_pnl=acct.unrealised_pnl,
            open_positions=len(self.open_spreads),
        )
        logger.info("Daily snapshot saved")
        log_event("SNAPSHOT", "Daily snapshot saved",
                  f"Equity: £{acct.equity:,.0f}, Spreads: {len(self.open_spreads)}")

    # ─── External controls (for API engine) ───────────────────────────────

    def request_stop(self):
        """Request graceful shutdown on next loop tick."""
        self.running = False

    def set_paused(self, paused: bool) -> str:
        """Pause/resume scheduled scans while keeping process alive."""
        with self._control_lock:
            self.paused = paused
            state = "paused" if paused else "resumed"
            detail = "Scheduled scans disabled" if paused else "Scheduled scans enabled"
            log_event("POSITION", f"Options bot {state}", detail, strategy="IBS Credit Spreads")
            logger.info(f"Control action: bot {state}")
            return f"Bot {state}."

    def get_override_status(self) -> dict:
        with self._control_lock:
            self._clear_expired_cooldowns()
            return {
                "kill_switch_active": self.kill_switch_active,
                "kill_switch_reason": self.kill_switch_reason,
                "risk_throttle_pct": self.risk_throttle_pct,
                "cooldowns": {
                    ticker: until.isoformat()
                    for ticker, until in sorted(self.market_cooldowns.items())
                },
                "cooldowns_count": len(self.market_cooldowns),
            }

    def set_kill_switch(self, active: bool, reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            self.kill_switch_active = active
            if active:
                self.kill_switch_reason = reason.strip() or "Manual operator kill switch"
            else:
                self.kill_switch_reason = ""
            self._persist_control_state()

            state = "ENABLED" if active else "DISABLED"
            detail = self.kill_switch_reason if active else (reason.strip() or "Manual clear")
            log_control_action(
                action="kill_switch",
                value=state,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Kill switch {state}", detail, strategy="IBS Credit Spreads")
            logger.warning(f"Control action: kill switch {state} ({detail})")
            return f"Kill switch {state.lower()}."

    def set_risk_throttle(self, pct: float, reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            clamped = min(1.0, max(0.1, float(pct)))
            self.risk_throttle_pct = clamped
            self._persist_control_state()

            pct_text = f"{clamped * 100:.0f}%"
            detail = reason.strip() or "Operator override"
            log_control_action(
                action="risk_throttle",
                value=pct_text,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Risk throttle set to {pct_text}", detail, strategy="IBS Credit Spreads")
            logger.info(f"Control action: risk throttle {pct_text} ({detail})")
            return f"Risk throttle set to {pct_text}."

    def set_market_cooldown(self, ticker: str, minutes: int,
                            reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            clean_ticker = str(ticker or "").upper().strip()
            if not clean_ticker:
                return "Ticker is required."
            duration = max(1, int(minutes))
            until = datetime.now(UK) + timedelta(minutes=duration)
            self.market_cooldowns[clean_ticker] = until
            self._persist_control_state()

            detail = reason.strip() or "Operator cooldown"
            value = f"{clean_ticker} until {until.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            log_control_action(
                action="market_cooldown_set",
                value=value,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Cooldown set for {clean_ticker}",
                      f"{duration}m ({detail})", ticker=clean_ticker, strategy="IBS Credit Spreads")
            logger.info(f"Control action: cooldown set {clean_ticker} for {duration}m")
            return f"Cooldown set for {clean_ticker} ({duration}m)."

    def clear_market_cooldown(self, ticker: str, reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            clean_ticker = str(ticker or "").upper().strip()
            if not clean_ticker:
                return "Ticker is required."
            existed = clean_ticker in self.market_cooldowns
            self.market_cooldowns.pop(clean_ticker, None)
            self._persist_control_state()

            detail = reason.strip() or "Operator cooldown clear"
            log_control_action(
                action="market_cooldown_clear",
                value=clean_ticker,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Cooldown cleared for {clean_ticker}",
                      detail, ticker=clean_ticker, strategy="IBS Credit Spreads")
            logger.info(f"Control action: cooldown cleared {clean_ticker}")
            if existed:
                return f"Cooldown cleared for {clean_ticker}."
            return f"No active cooldown for {clean_ticker}."

    def run_manual_scan(self) -> dict:
        """Run an immediate signal scan from control plane."""
        with self._control_lock:
            logger.info("Control action: running manual signal scan now")
            log_event("SCAN", "Manual scan requested", "Triggered from control plane")
            self._run_all_signals()
            return {
                "ok": True,
                "message": "Manual signal scan completed.",
                "tickers": config.LIVE_TRADING_TICKERS,
            }

    def build_reconcile_report(self) -> dict:
        """Build a structured reconcile report: DB vs in-memory vs broker."""
        with self._control_lock:
            db_positions = get_open_option_positions()
            memory_positions = list(self.open_spreads.values())

            db_spread_ids = set(str(p.get("spread_id", "")) for p in db_positions if p.get("spread_id"))
            mem_spread_ids = set(str(p.get("spread_id", "")) for p in memory_positions if p.get("spread_id"))
            db_only_spread_ids = sorted(db_spread_ids - mem_spread_ids)
            memory_only_spread_ids = sorted(mem_spread_ids - db_spread_ids)

            db_deal_ids = set()
            for pos in db_positions:
                short_id = str(pos.get("short_deal_id", "") or "").strip()
                long_id = str(pos.get("long_deal_id", "") or "").strip()
                if short_id:
                    db_deal_ids.add(short_id)
                if long_id:
                    db_deal_ids.add(long_id)

            broker_deal_ids = set()
            broker_error = ""
            if self.broker:
                try:
                    for broker_pos in self.broker.get_positions():
                        deal_id = str(getattr(broker_pos, "deal_id", "") or "").strip()
                        if deal_id:
                            broker_deal_ids.add(deal_id)
                except Exception as exc:
                    broker_error = str(exc)

            db_only_deal_ids = sorted(db_deal_ids - broker_deal_ids)
            broker_only_deal_ids = sorted(broker_deal_ids - db_deal_ids)

            suggestions = []
            if db_only_spread_ids:
                suggestions.append(
                    "Sync runtime state from DB (manual reconcile) before taking manual close actions."
                )
            if memory_only_spread_ids:
                suggestions.append(
                    "In-memory spreads not present in DB; review recent crash and persist/clear those entries."
                )
            if db_only_deal_ids:
                suggestions.append(
                    "DB contains deal IDs absent at broker; likely stale records. Investigate and close stale spreads."
                )
            if broker_only_deal_ids:
                suggestions.append(
                    "Broker has unmanaged deal IDs; import them into DB or close manually in IG."
                )
            if not suggestions:
                suggestions.append("No mismatches detected.")

            return {
                "db_open_spreads": len(db_spread_ids),
                "memory_open_spreads": len(mem_spread_ids),
                "broker_open_deals": len(broker_deal_ids),
                "db_only_spread_ids": db_only_spread_ids,
                "memory_only_spread_ids": memory_only_spread_ids,
                "db_only_deal_ids": db_only_deal_ids,
                "broker_only_deal_ids": broker_only_deal_ids,
                "has_mismatch": bool(
                    db_only_spread_ids or memory_only_spread_ids or db_only_deal_ids or broker_only_deal_ids
                ),
                "broker_error": broker_error,
                "suggestions": suggestions,
                "generated_at": datetime.now(UK).isoformat(),
            }

    def reconcile_now(self) -> dict:
        """Rebuild in-memory state from DB + account snapshot."""
        with self._control_lock:
            db_positions = get_open_option_positions()
            previous_ids = set(self.open_spreads.keys())

            self.open_spreads = {p["spread_id"]: p for p in db_positions}
            current_ids = set(self.open_spreads.keys())

            if self.broker:
                acct = self.broker.get_account_info()
                if acct.equity > 0:
                    self.safety.equity = acct.equity
            self.safety.update_state(self.safety.equity, list(self.open_spreads.values()))
            added = sorted(current_ids - previous_ids)
            removed = sorted(previous_ids - current_ids)
            report = self.build_reconcile_report()
            detail = (
                f"added={len(added)} removed={len(removed)} open={len(current_ids)} "
                f"db_only_deals={len(report['db_only_deal_ids'])} "
                f"broker_only_deals={len(report['broker_only_deal_ids'])}"
            )
            logger.info(f"Control action: reconcile complete ({detail})")
            if report["broker_error"]:
                log_event(
                    "ERROR",
                    "Manual reconcile broker fetch failed",
                    report["broker_error"],
                    strategy="IBS Credit Spreads",
                )
            if report["has_mismatch"]:
                log_event(
                    "ERROR",
                    "Manual reconcile found DB/Broker mismatches",
                    detail,
                    strategy="IBS Credit Spreads",
                )
            else:
                log_event("POSITION", "Manual reconcile complete", detail, strategy="IBS Credit Spreads")
            return {
                "ok": True,
                "message": f"Reconcile complete ({detail}).",
                "added": added,
                "removed": removed,
                "open_count": len(current_ids),
                "db_only_deal_ids": report["db_only_deal_ids"],
                "broker_only_deal_ids": report["broker_only_deal_ids"],
                "mismatch": report["has_mismatch"],
                "report": report,
            }

    def close_spread_manual(self, spread_id: str = "", ticker: str = "",
                            reason: str = "Manual close from control plane") -> dict:
        """Close a specific spread by spread_id or ticker."""
        with self._control_lock:
            spread = None
            if spread_id:
                spread = self.open_spreads.get(spread_id)
            elif ticker:
                spread = self._get_open_spread_dict(ticker)

            if not spread:
                return {"ok": False, "message": "No matching open spread found."}

            ok = self._close_spread(spread, reason)
            if not ok:
                return {"ok": False, "message": "Spread close attempt failed; see logs."}
            return {
                "ok": True,
                "message": f"Closed spread {spread.get('spread_id', '')} ({spread.get('ticker', '')}).",
            }

    # ─── Shutdown ──────────────────────────────────────────────────────────

    def _shutdown(self, signum=None, frame=None):
        logger.info("\nShutdown signal received...")
        self.running = False

    def _shutdown_clean(self):
        logger.info("Saving final state...")
        self._daily_snapshot()

        log_event("SHUTDOWN", f"Options bot stopped ({self.mode.upper()})",
                  f"{len(self.open_spreads)} spreads still open")
        notifier.send(
            f"Bot stopped — {self.mode.upper()} mode\n"
            f"{len(self.open_spreads)} spreads still open",
            icon="🛑",
        )

        if self.broker:
            self.broker.disconnect()
        logger.info("Bot stopped.")


def main():
    parser = argparse.ArgumentParser(description="Options Credit Spread Auto-Trader")
    parser.add_argument("--mode", choices=["shadow", "live"],
                        default=config.TRADING_MODE,
                        help=f"Trading mode (default: {config.TRADING_MODE})")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one signal scan and exit (for API/manual trigger use).",
    )
    args = parser.parse_args()

    setup_logging()

    if args.mode == "live":
        logger.warning("=" * 60)
        logger.warning("  LIVE MODE — REAL MONEY WILL BE TRADED")
        logger.warning("  Press Ctrl+C within 5 seconds to abort")
        logger.warning("=" * 60)
        time.sleep(5)

    bot = OptionsBot(mode=args.mode)
    bot.start(once=args.once)


if __name__ == "__main__":
    main()
