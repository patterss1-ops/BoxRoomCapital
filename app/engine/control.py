"""Control-plane service for in-process options engine lifecycle."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import config
from app.engine.options_engine import OptionsEngine

logger = logging.getLogger(__name__)


class BotControlService:
    """Facade used by API routes to control the in-process options engine."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.runtime_dir = self.project_root / ".runtime"
        self.runtime_dir.mkdir(exist_ok=True)
        self.state_file = self.runtime_dir / "options_engine_state.json"
        self.process_log = self.project_root / config.LOG_FILE
        self.engine = OptionsEngine()

        # Scheduler and dispatcher state
        self._scheduler: Optional[Any] = None
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._dispatcher_stop_event = threading.Event()
        self._last_dag_result: Optional[dict] = None
        self._intraday_loop: Optional[Any] = None

    def _write_state(self, data: dict[str, Any]):
        payload = {
            "updated_at": datetime.now().isoformat(),
            **data,
        }
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def status(self) -> dict[str, Any]:
        engine_state = self.engine.status()
        persisted = {}
        if self.state_file.exists():
            try:
                persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                persisted = {}

        return {
            **engine_state,
            "log_file": str(self.process_log),
            "last_action": persisted.get("last_action"),
            "last_action_at": persisted.get("updated_at"),
        }

    def start(self, mode: str) -> dict[str, Any]:
        result = self.engine.start(mode=mode)
        self._write_state({"last_action": f"start:{mode}"})
        return result

    def stop(self) -> dict[str, Any]:
        result = self.engine.stop()
        self._write_state({"last_action": "stop"})
        return result

    def pause(self) -> dict[str, Any]:
        result = self.engine.pause()
        self._write_state({"last_action": "pause"})
        return result

    def resume(self) -> dict[str, Any]:
        result = self.engine.resume()
        self._write_state({"last_action": "resume"})
        return result

    def scan_once(self, mode: str) -> dict[str, Any]:
        result = self.engine.scan_now(mode=mode)
        self._write_state({"last_action": f"scan:{mode}"})
        return result

    def reconcile(self) -> dict[str, Any]:
        result = self.engine.reconcile()
        self._write_state({"last_action": "reconcile"})
        return result

    def reconcile_report(self) -> dict[str, Any]:
        return self.engine.reconcile_report()

    def close_spread(self, spread_id: str = "", ticker: str = "", reason: str = "Manual close") -> dict[str, Any]:
        result = self.engine.close_spread(spread_id=spread_id, ticker=ticker, reason=reason)
        target = spread_id or ticker or "unknown"
        self._write_state({"last_action": f"close:{target}"})
        return result

    def set_kill_switch(self, active: bool, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.set_kill_switch(active=active, reason=reason, actor=actor)
        action = "kill-on" if active else "kill-off"
        self._write_state({"last_action": f"{action}"})
        return result

    def set_risk_throttle(self, pct: float, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.set_risk_throttle(pct=pct, reason=reason, actor=actor)
        self._write_state({"last_action": f"risk-throttle:{pct}"})
        return result

    def set_market_cooldown(self, ticker: str, minutes: int, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.set_market_cooldown(ticker=ticker, minutes=minutes, reason=reason, actor=actor)
        self._write_state({"last_action": f"cooldown-set:{ticker}:{minutes}"})
        return result

    def clear_market_cooldown(self, ticker: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.clear_market_cooldown(ticker=ticker, reason=reason, actor=actor)
        self._write_state({"last_action": f"cooldown-clear:{ticker}"})
        return result

    # ─── Scheduler lifecycle ──────────────────────────────────────────────

    def start_scheduler(self) -> dict[str, Any]:
        """Start the daily workflow scheduler in a background thread."""
        if self._scheduler is not None:
            return {"status": "already_running"}

        from app.engine.pipeline import dispatch_orchestration
        from app.engine.scheduler import DailyWorkflowScheduler

        self._scheduler = DailyWorkflowScheduler(
            dispatch_fn=lambda window_name, **kw: dispatch_orchestration(
                dry_run=config.ORCHESTRATOR_DRY_RUN,
                ai_panel_enabled=config.AI_PANEL_ENABLED,
            ),
        )
        self._scheduler.start()
        self._write_state({"last_action": "scheduler-start"})
        logger.info("Scheduler started")
        return {"status": "started"}

    def stop_scheduler(self) -> dict[str, Any]:
        """Stop the daily workflow scheduler."""
        if self._scheduler is None:
            return {"status": "not_running"}

        self._scheduler.stop()
        self._scheduler = None
        self._write_state({"last_action": "scheduler-stop"})
        logger.info("Scheduler stopped")
        return {"status": "stopped"}

    def scheduler_status(self) -> dict[str, Any]:
        """Return scheduler state."""
        if self._scheduler is None:
            return {"running": False}
        return {
            "running": True,
            **self._scheduler.status(),
        }

    # ─── Dispatcher lifecycle ─────────────────────────────────────────────

    def start_dispatcher(self) -> dict[str, Any]:
        """Start the intent dispatcher loop in a background thread."""
        if self._dispatcher_thread is not None and self._dispatcher_thread.is_alive():
            return {"status": "already_running"}

        self._dispatcher_stop_event.clear()

        def _dispatcher_loop():
            from execution.dispatcher import IntentDispatcher
            dispatcher = IntentDispatcher()
            while not self._dispatcher_stop_event.is_set():
                try:
                    dispatcher.run_once()
                except Exception as exc:
                    logger.warning("Dispatcher cycle error: %s", exc)
                self._dispatcher_stop_event.wait(timeout=config.DISPATCHER_INTERVAL_SECONDS)

        self._dispatcher_thread = threading.Thread(
            target=_dispatcher_loop, name="intent-dispatcher", daemon=True
        )
        self._dispatcher_thread.start()
        self._write_state({"last_action": "dispatcher-start"})
        logger.info("Dispatcher started (interval=%ds)", config.DISPATCHER_INTERVAL_SECONDS)
        return {"status": "started", "interval_seconds": config.DISPATCHER_INTERVAL_SECONDS}

    def stop_dispatcher(self) -> dict[str, Any]:
        """Stop the intent dispatcher loop."""
        if self._dispatcher_thread is None or not self._dispatcher_thread.is_alive():
            return {"status": "not_running"}

        self._dispatcher_stop_event.set()
        self._dispatcher_thread.join(timeout=15)
        self._dispatcher_thread = None
        self._write_state({"last_action": "dispatcher-stop"})
        logger.info("Dispatcher stopped")
        return {"status": "stopped"}

    def dispatcher_status(self) -> dict[str, Any]:
        """Return dispatcher state."""
        running = self._dispatcher_thread is not None and self._dispatcher_thread.is_alive()
        return {"running": running}

    # ─── Daily DAG ────────────────────────────────────────────────────────

    def trigger_daily_dag(self) -> dict[str, Any]:
        """Run the full daily trading DAG synchronously."""
        from app.engine.trading_dag import run_daily_dag

        self._write_state({"last_action": "dag-start"})
        try:
            result = run_daily_dag()
            summary = {
                "status": result.status.value,
                "duration": round(result.duration, 1),
                "nodes": {
                    name: {"status": nr.status.value, "duration": round(nr.duration, 1)}
                    for name, nr in result.node_results.items()
                },
            }
            self._last_dag_result = summary
            self._write_state({"last_action": "dag-complete"})
            return summary
        except Exception as exc:
            error_result = {"status": "failed", "error": str(exc)}
            self._last_dag_result = error_result
            self._write_state({"last_action": "dag-failed"})
            return error_result

    def pipeline_status(self) -> dict[str, Any]:
        """Return combined pipeline state (scheduler + dispatcher + intraday + last DAG)."""
        return {
            "scheduler": self.scheduler_status(),
            "dispatcher": self.dispatcher_status(),
            "intraday": self.intraday_status(),
            "last_dag_result": self._last_dag_result,
            "config": {
                "orchestrator_enabled": config.ORCHESTRATOR_ENABLED,
                "orchestrator_dry_run": config.ORCHESTRATOR_DRY_RUN,
                "ai_panel_enabled": config.AI_PANEL_ENABLED,
                "dispatcher_enabled": config.DISPATCHER_ENABLED,
                "dispatcher_interval_seconds": config.DISPATCHER_INTERVAL_SECONDS,
                "intraday_enabled": config.INTRADAY_ENABLED,
                "intraday_poll_seconds": config.INTRADAY_POLL_SECONDS,
            },
        }

    # ─── Intraday event loop ────────────────────────────────────────────

    def start_intraday(self) -> dict[str, Any]:
        """Start the intraday polling loop."""
        if self._intraday_loop is not None:
            return {"status": "already_running"}

        from app.engine.intraday import IntradayEventLoop

        self._intraday_loop = IntradayEventLoop(
            poll_interval=config.INTRADAY_POLL_SECONDS,
            tickers=config.INTRADAY_TICKERS,
        )
        result = self._intraday_loop.start()
        self._write_state({"last_action": "intraday-start"})
        logger.info("Intraday loop started")
        return result

    def stop_intraday(self) -> dict[str, Any]:
        """Stop the intraday polling loop."""
        if self._intraday_loop is None:
            return {"status": "not_running"}

        result = self._intraday_loop.stop()
        self._intraday_loop = None
        self._write_state({"last_action": "intraday-stop"})
        logger.info("Intraday loop stopped")
        return result

    def intraday_status(self) -> dict[str, Any]:
        """Return intraday loop state."""
        if self._intraday_loop is None:
            return {"running": False}
        return self._intraday_loop.status()

    # ─── Supervision / watchdog ──────────────────────────────────────────

    def check_and_restart(self) -> dict[str, Any]:
        """Check if scheduler/dispatcher crashed and restart them.

        Called periodically by the supervision loop to ensure always-on operation.
        Returns a dict describing what was restarted (empty if all healthy).
        """
        restarted = {}

        # Restart scheduler if it was supposed to be running but thread died
        if config.ORCHESTRATOR_ENABLED and self._scheduler is None:
            try:
                result = self.start_scheduler()
                restarted["scheduler"] = result.get("status", "unknown")
                logger.warning("Supervisor restarted crashed scheduler")
            except Exception as exc:
                logger.error("Supervisor failed to restart scheduler: %s", exc)

        # Restart dispatcher if thread died
        if config.DISPATCHER_ENABLED:
            thread_dead = (
                self._dispatcher_thread is None
                or not self._dispatcher_thread.is_alive()
            )
            if thread_dead and not self._dispatcher_stop_event.is_set():
                try:
                    result = self.start_dispatcher()
                    restarted["dispatcher"] = result.get("status", "unknown")
                    logger.warning("Supervisor restarted crashed dispatcher")
                except Exception as exc:
                    logger.error("Supervisor failed to restart dispatcher: %s", exc)

        # Restart intraday loop if it crashed
        if config.INTRADAY_ENABLED and self._intraday_loop is None:
            try:
                result = self.start_intraday()
                restarted["intraday"] = result.get("status", "unknown")
                logger.warning("Supervisor restarted crashed intraday loop")
            except Exception as exc:
                logger.error("Supervisor failed to restart intraday loop: %s", exc)

        return restarted
