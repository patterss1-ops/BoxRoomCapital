"""In-process runtime engine for OptionsBot lifecycle and control actions."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from data.trade_db import get_conn, log_event
from options_runner import OptionsBot
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


class OptionsEngine:
    """Runs OptionsBot in a background thread and exposes control actions."""

    def __init__(self):
        self._lock = threading.RLock()
        self._bot: OptionsBot | None = None
        self._thread: threading.Thread | None = None
        self._mode = "shadow"
        self._started_at: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            paused = bool(self._bot.paused) if running and self._bot else False
            open_spreads = len(self._bot.open_spreads) if running and self._bot else 0
            overrides = self._bot.get_override_status() if running and self._bot else {
                "kill_switch_active": False,
                "kill_switch_reason": "",
                "risk_throttle_pct": 1.0,
                "cooldowns": {},
                "cooldowns_count": 0,
            }
            return {
                "running": running,
                "paused": paused,
                "mode": self._mode,
                "started_at": self._started_at,
                "thread_name": self._thread.name if running and self._thread else None,
                "open_spreads": open_spreads,
                "last_error": self._last_error,
                **overrides,
            }

    def start(self, mode: str) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": False, "message": "Bot already running."}

            setup_logging()
            self._mode = mode
            self._last_error = None
            self._started_at = datetime.now().isoformat()
            try:
                self._bot = OptionsBot(mode=mode)
            except Exception as exc:
                self._last_error = str(exc)
                self._started_at = None
                logger.warning("OptionsBot constructor failed: %s", exc)
                return {"ok": False, "message": f"Bot failed to initialise: {exc}"}
            self._thread = threading.Thread(
                target=self._run_bot_thread,
                name="options-engine",
                daemon=True,
            )
            self._thread.start()

        # brief settle to detect immediate startup crash
        time.sleep(0.25)
        status = self.status()
        if not status["running"]:
            return {"ok": False, "message": "Bot exited immediately. Check logs and credentials."}

        log_event(
            "STARTUP",
            f"Control plane started in-process options engine ({mode.upper()})",
            strategy="IBS Credit Spreads",
        )
        return {"ok": True, "message": f"Started bot in {mode.upper()} mode."}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not (self._thread and self._thread.is_alive() and self._bot):
                return {"ok": False, "message": "Bot is not running."}

            self._bot.request_stop()
            thread = self._thread

        thread.join(timeout=20)
        status = self.status()
        if status["running"]:
            return {"ok": False, "message": "Bot did not stop within timeout."}

        log_event("SHUTDOWN", "Control plane stopped options engine", strategy="IBS Credit Spreads")
        return {"ok": True, "message": "Bot stopped."}

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if not self._bot or not (self._thread and self._thread.is_alive()):
                return {"ok": False, "message": "Bot is not running."}
            msg = self._bot.set_paused(True)
            return {"ok": True, "message": msg}

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if not self._bot or not (self._thread and self._thread.is_alive()):
                return {"ok": False, "message": "Bot is not running."}
            msg = self._bot.set_paused(False)
            return {"ok": True, "message": msg}

    def scan_now(self, mode: str) -> dict[str, Any]:
        """
        Run an immediate scan.
        If engine is running, use live instance. If not, run one-shot in-process.
        """
        with self._lock:
            running_bot = self._bot if self._bot and self._thread and self._thread.is_alive() else None

        if running_bot:
            return running_bot.run_manual_scan()

        setup_logging()
        try:
            temp = OptionsBot(mode=mode)
            ok = temp.start(once=True, install_signal_handlers=False)
        except Exception as exc:
            logger.warning("One-shot scan failed: %s", exc)
            return {"ok": False, "message": f"Scan failed: {exc}"}
        if ok:
            return {"ok": True, "message": f"One-shot scan complete ({mode.upper()})."}
        return {"ok": False, "message": "One-shot scan failed. Check logs."}

    def reconcile(self) -> dict[str, Any]:
        with self._lock:
            if not self._bot or not (self._thread and self._thread.is_alive()):
                return {"ok": False, "message": "Bot is not running."}
            return self._bot.reconcile_now()

    def reconcile_report(self) -> dict[str, Any]:
        with self._lock:
            if not self._bot or not (self._thread and self._thread.is_alive()):
                return {
                    "ok": False,
                    "message": "Bot is not running.",
                    "report": {
                        "db_open_spreads": 0,
                        "memory_open_spreads": 0,
                        "broker_open_deals": 0,
                        "db_only_spread_ids": [],
                        "memory_only_spread_ids": [],
                        "db_only_deal_ids": [],
                        "broker_only_deal_ids": [],
                        "has_mismatch": False,
                        "broker_error": "",
                        "suggestions": ["Start bot to generate live reconcile report."],
                    },
                }
            return {"ok": True, "message": "Reconcile report generated.", "report": self._bot.build_reconcile_report()}

    def close_spread(self, spread_id: str = "", ticker: str = "", reason: str = "Manual close") -> dict[str, Any]:
        with self._lock:
            if not self._bot or not (self._thread and self._thread.is_alive()):
                return {"ok": False, "message": "Bot is not running."}
            return self._bot.close_spread_manual(spread_id=spread_id, ticker=ticker, reason=reason)

    def _persist_state(self, key: str, value: Any) -> None:
        """Write a control state value to strategy_state for persistence."""
        try:
            conn = get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO strategy_state (key, value, updated) VALUES (?,?,?)",
                (key, json.dumps(value) if not isinstance(value, str) else value,
                 datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to persist state %s: %s", key, exc)

    def set_kill_switch(self, active: bool, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        with self._lock:
            if self._bot and self._thread and self._thread.is_alive():
                message = self._bot.set_kill_switch(active=active, reason=reason, actor=actor)
                return {"ok": True, "message": message}

        # Persist to strategy_state when bot is not running
        self._persist_state("kill_switch_active", "true" if active else "false")
        self._persist_state("kill_switch_reason", reason)
        action = "enabled" if active else "disabled"
        log_event("KILL_SWITCH", f"Kill switch {action} by {actor}: {reason}")
        return {"ok": True, "message": f"Kill switch {action} (persisted). {reason}"}

    def set_risk_throttle(self, pct: float, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        with self._lock:
            if self._bot and self._thread and self._thread.is_alive():
                message = self._bot.set_risk_throttle(pct=pct, reason=reason, actor=actor)
                return {"ok": True, "message": message}

        self._persist_state("risk_throttle_pct", pct)
        log_event("RISK_THROTTLE", f"Risk throttle set to {pct*100:.0f}% by {actor}: {reason}")
        return {"ok": True, "message": f"Risk throttle set to {pct*100:.0f}% (persisted). {reason}"}

    def set_market_cooldown(self, ticker: str, minutes: int,
                            reason: str = "", actor: str = "operator") -> dict[str, Any]:
        with self._lock:
            if self._bot and self._thread and self._thread.is_alive():
                message = self._bot.set_market_cooldown(ticker=ticker, minutes=minutes, reason=reason, actor=actor)
                return {"ok": True, "message": message}

        self._persist_state(f"cooldown_{ticker}", {"minutes": minutes, "reason": reason, "actor": actor})
        log_event("COOLDOWN", f"Cooldown set on {ticker} for {minutes}m by {actor}: {reason}")
        return {"ok": True, "message": f"Cooldown set on {ticker} for {minutes}m (persisted). {reason}"}

    def clear_market_cooldown(self, ticker: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        with self._lock:
            if self._bot and self._thread and self._thread.is_alive():
                message = self._bot.clear_market_cooldown(ticker=ticker, reason=reason, actor=actor)
                return {"ok": True, "message": message}

        self._persist_state(f"cooldown_{ticker}", None)
        log_event("COOLDOWN", f"Cooldown cleared on {ticker} by {actor}: {reason}")
        return {"ok": True, "message": f"Cooldown cleared on {ticker} (persisted). {reason}"}

    def _run_bot_thread(self):
        bot = None
        with self._lock:
            bot = self._bot

        if not bot:
            return

        try:
            ok = bot.start(once=False, install_signal_handlers=False)
            if not ok:
                with self._lock:
                    self._last_error = "Bot failed during startup."
        except Exception as exc:  # pragma: no cover - defensive path
            with self._lock:
                self._last_error = str(exc)
            log_event("ERROR", "Options engine crashed", str(exc), strategy="IBS Credit Spreads")
        finally:
            with self._lock:
                self._bot = None
                self._thread = None
                self._started_at = None
