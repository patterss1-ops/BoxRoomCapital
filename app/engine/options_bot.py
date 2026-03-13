"""Core OptionsBot class with lifecycle, tick loop, and helpers."""
from __future__ import annotations

import logging
import signal
import time
import threading
import uuid
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import config
from data.provider import DataProvider
from broker.ig import IGBroker
from safety_controller import SafetyController, SafetyLimits
from notifications import notifier
from data.trade_db import log_event, save_strategy_state
from utils.logger import setup_logging

from app.engine.options_signals import OptionsSignalsMixin
from app.engine.options_spreads import OptionsSpreadsMixin
from app.engine.options_controls import OptionsControlsMixin
from app.engine.options_recovery import OptionsRecoveryMixin

logger = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")

SCHEDULE = {
    "eu_close": {"hour": 17, "minute": 5},
    "us_close": {"hour": 21, "minute": 15},
    "daily_snapshot": {"hour": 22, "minute": 0},
}

EU_TICKERS = ["EWU", "EWG", "EWJ"]
US_TICKERS = ["SPY", "QQQ", "GLD"]

POSITION_CHECK_INTERVAL = 300
HEARTBEAT_INTERVAL = 7200
ORDER_ACTION_MAX_ATTEMPTS = 3


class OptionsBot(
    OptionsSignalsMixin,
    OptionsSpreadsMixin,
    OptionsControlsMixin,
    OptionsRecoveryMixin,
):
    """
    Auto-trading bot for IBS Credit Spreads on IG.
    Shadow mode by default — toggle to live via --mode live.
    """

    def __init__(self, mode: str = "shadow"):
        self.mode = mode
        self.running = False
        self.paused = False

        self.broker = None
        self.data = DataProvider(lookback_days=500)
        self.safety = SafetyController(
            initial_equity=5000,
            limits=SafetyLimits(**{
                k: v for k, v in config.OPTIONS_SAFETY.items()
                if k in SafetyLimits.__dataclass_fields__
            }),
        )

        self.open_spreads: dict[str, dict] = {}

        self._today_signals: set = set()
        self._last_eu_check = None
        self._last_us_check = None
        self._last_snapshot = None
        self._last_heartbeat = None
        self._last_position_check = None
        self._pause_announced = False
        self._control_lock = threading.RLock()

        self.kill_switch_active = False
        self.kill_switch_reason = ""
        self.risk_throttle_pct = 1.0
        self.market_cooldowns: dict[str, datetime] = {}
        self.strategy_params: dict = dict(config.IBS_CREDIT_SPREAD_PARAMS)
        self.active_param_set_id: Optional[str] = None
        self.active_param_set_status: str = "default"

    @property
    def is_shadow(self) -> bool:
        return self.mode == "shadow"

    def start(self, once: bool = False, install_signal_handlers: bool = True) -> bool:
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

        if self.mode == "live":
            self.broker = IGBroker(is_demo=False)
        elif self.mode == "demo":
            self.broker = IGBroker(is_demo=True)
        else:
            self.broker = IGBroker(is_demo=config.ig_broker_is_demo())

        if not self.broker.connect():
            logger.error("Failed to connect to IG. Aborting.")
            notifier.error("Bot startup failed — cannot connect to IG")
            return False

        acct = self.broker.get_account_info()
        if acct.equity > 0:
            self.safety.equity = acct.equity
            logger.info(f"Account equity: £{acct.equity:,.2f}")

        self._load_open_positions()
        self._load_control_state()
        self._startup_recover()

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

        logger.info("\nRunning initial signal scan...")
        self._run_all_signals()

        if once:
            logger.info("One-shot mode complete. Shutting down.")
            self.running = False
            self._shutdown_clean()
            return True

        logger.info("\nBot is running. Press Ctrl+C to stop.\n")

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
        with self._control_lock:
            now = datetime.now(UK)
            today = now.date()
            self._clear_expired_cooldowns()

            if self._last_eu_check and self._last_eu_check != today:
                self._today_signals.clear()

            if self._last_heartbeat is None or (now - self._last_heartbeat).seconds >= HEARTBEAT_INTERVAL:
                self._heartbeat(now)
                self._last_heartbeat = now

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

            if (now.hour == SCHEDULE["eu_close"]["hour"]
                and now.minute >= SCHEDULE["eu_close"]["minute"]
                and self._last_eu_check != today):
                logger.info("\n── EUROPEAN CLOSE ──")
                log_event("SCAN", "EU close scan", f"Checking {', '.join(EU_TICKERS)}")
                self._run_signals(EU_TICKERS)
                self._last_eu_check = today

            if (now.hour == SCHEDULE["us_close"]["hour"]
                and now.minute >= SCHEDULE["us_close"]["minute"]
                and self._last_us_check != today):
                logger.info("\n── US CLOSE ──")
                log_event("SCAN", "US close scan", f"Checking {', '.join(US_TICKERS)}")
                self._run_signals(US_TICKERS)
                self._last_us_check = today

            if (8 <= now.hour <= 22
                and (self._last_position_check is None
                     or (now - self._last_position_check).seconds >= POSITION_CHECK_INTERVAL)):
                self._monitor_positions()
                self._last_position_check = now

            if (now.hour == SCHEDULE["daily_snapshot"]["hour"]
                and self._last_snapshot != today):
                self._daily_snapshot()
                self._last_snapshot = today

    def _new_correlation_id(self, action: str, ticker: str) -> str:
        stamp = datetime.now(UK).strftime("%m%d%H%M%S")
        uid = uuid.uuid4().hex[:4]
        return f"{ticker}-{stamp}-{uid}"

    def _classify_order_error(self, message: str, code_hint: str = "") -> tuple[str, bool]:
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
        return float(min(10, attempt * 2))

    def _heartbeat(self, now):
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
