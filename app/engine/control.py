"""Control-plane service for in-process options engine lifecycle."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import config
from app.engine.options_engine import OptionsEngine

logger = logging.getLogger(__name__)


class BotControlService:
    """Facade used by API routes to control the in-process options engine."""

    def __init__(
        self,
        project_root: Path,
        engine_a_factory: Optional[Callable[[], Any]] = None,
        engine_b_factory: Optional[Callable[[], Any]] = None,
        decay_review_factory: Optional[Callable[[], Any]] = None,
        kill_monitor_factory: Optional[Callable[[], Any]] = None,
    ):
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
        self._engine_a_factory = engine_a_factory
        self._engine_b_factory = engine_b_factory
        self._decay_review_factory = decay_review_factory
        self._kill_monitor_factory = kill_monitor_factory
        self._engine_a_pipeline: Optional[Any] = None
        self._engine_a_thread: Optional[threading.Thread] = None
        self._engine_a_stop_event = threading.Event()
        self._last_engine_a_result: Optional[dict[str, Any]] = None
        self._engine_b_pipeline: Optional[Any] = None
        self._engine_b_thread: Optional[threading.Thread] = None
        self._engine_b_stop_event = threading.Event()
        self._engine_b_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._last_engine_b_result: Optional[dict[str, Any]] = None
        self._last_decay_review_result: Optional[dict[str, Any]] = None
        self._last_kill_check_result: Optional[dict[str, Any]] = None

    def configure_research_services(
        self,
        *,
        engine_a_factory: Optional[Callable[[], Any]] = None,
        engine_b_factory: Optional[Callable[[], Any]] = None,
        decay_review_factory: Optional[Callable[[], Any]] = None,
        kill_monitor_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Attach research service factories after construction."""
        if engine_a_factory is not None:
            self._engine_a_factory = engine_a_factory
        if engine_b_factory is not None:
            self._engine_b_factory = engine_b_factory
        if decay_review_factory is not None:
            self._decay_review_factory = decay_review_factory
        if kill_monitor_factory is not None:
            self._kill_monitor_factory = kill_monitor_factory

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
        from app.engine.scheduler import DEFAULT_SCHEDULE, DailyWorkflowScheduler, ScheduleWindow

        schedule = list(DEFAULT_SCHEDULE)
        window_handlers: dict[str, Callable[..., Any]] = {}

        if config.ENGINE_A_ENABLED and self._engine_a_factory is not None:
            schedule.append(ScheduleWindow(name="engine_a_close_research", hour=21, minute=30))
            window_handlers["engine_a_close_research"] = self._run_engine_a_window

        if self._decay_review_factory is not None:
            for hour in (0, 6, 12, 18):
                name = f"research_decay_review_{hour:02d}"
                schedule.append(ScheduleWindow(name=name, hour=hour, minute=0))
                window_handlers[name] = self._run_decay_review_window

        if self._kill_monitor_factory is not None:
            for hour in range(14, 21):
                name = f"research_kill_check_{hour:02d}"
                schedule.append(ScheduleWindow(name=name, hour=hour, minute=0))
                window_handlers[name] = self._run_kill_check_window

        self._scheduler = DailyWorkflowScheduler(
            dispatch_fn=lambda window_name, **kw: dispatch_orchestration(
                dry_run=config.ORCHESTRATOR_DRY_RUN,
                ai_panel_enabled=config.AI_PANEL_ENABLED,
            ),
            schedule=schedule,
            window_handlers=window_handlers,
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

    # ─── Engine A lifecycle ──────────────────────────────────────────────

    def start_engine_a(self) -> dict[str, Any]:
        """Start the Engine A daily loop in a background thread."""
        if not config.ENGINE_A_ENABLED:
            return {"status": "disabled"}
        if self._engine_a_factory is None:
            return {"status": "unavailable", "detail": "engine_a_factory not configured"}
        if self._engine_a_thread is not None and self._engine_a_thread.is_alive():
            return {"status": "already_running"}

        self._engine_a_pipeline = self._engine_a_factory()
        self._engine_a_stop_event.clear()

        def _engine_a_loop():
            while not self._engine_a_stop_event.is_set():
                as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                try:
                    result = self._engine_a_pipeline.run_daily(as_of)
                    self._last_engine_a_result = {
                        "status": "ok",
                        "as_of": as_of,
                        "artifacts": len(getattr(result, "artifacts", [])),
                    }
                except Exception as exc:
                    self._last_engine_a_result = {
                        "status": "failed",
                        "as_of": as_of,
                        "error": str(exc),
                    }
                    logger.warning("Engine A cycle error: %s", exc)
                self._engine_a_stop_event.wait(timeout=config.ENGINE_A_INTERVAL_SECONDS)

        self._engine_a_thread = threading.Thread(
            target=_engine_a_loop,
            name="engine-a-loop",
            daemon=True,
        )
        self._engine_a_thread.start()
        self._write_state({"last_action": "engine-a-start"})
        logger.info("Engine A started (interval=%ds)", config.ENGINE_A_INTERVAL_SECONDS)
        return {"status": "started", "interval_seconds": config.ENGINE_A_INTERVAL_SECONDS}

    def stop_engine_a(self) -> dict[str, Any]:
        """Stop the Engine A loop."""
        if self._engine_a_thread is None or not self._engine_a_thread.is_alive():
            return {"status": "not_running"}

        self._engine_a_stop_event.set()
        self._engine_a_thread.join(timeout=15)
        self._engine_a_thread = None
        self._write_state({"last_action": "engine-a-stop"})
        logger.info("Engine A stopped")
        return {"status": "stopped"}

    def engine_a_status(self) -> dict[str, Any]:
        """Return Engine A service state."""
        running = self._engine_a_thread is not None and self._engine_a_thread.is_alive()
        return {
            "running": running,
            "enabled": config.ENGINE_A_ENABLED,
            "configured": self._engine_a_factory is not None,
            "interval_seconds": config.ENGINE_A_INTERVAL_SECONDS,
            "last_result": self._last_engine_a_result,
        }

    # ─── Engine B lifecycle ──────────────────────────────────────────────

    def start_engine_b(self) -> dict[str, Any]:
        """Start the Engine B intake worker."""
        if not config.ENGINE_B_ENABLED:
            return {"status": "disabled"}
        if self._engine_b_factory is None:
            return {"status": "unavailable", "detail": "engine_b_factory not configured"}
        if self._engine_b_thread is not None and self._engine_b_thread.is_alive():
            return {"status": "already_running"}

        self._engine_b_pipeline = self._engine_b_factory()
        self._engine_b_stop_event.clear()
        self._engine_b_queue = queue.Queue()

        def _engine_b_loop():
            while not self._engine_b_stop_event.is_set():
                try:
                    job = self._engine_b_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if job is None:
                    break
                self._run_engine_b_job(job, self._engine_b_pipeline)

        self._engine_b_thread = threading.Thread(
            target=_engine_b_loop,
            name="engine-b-worker",
            daemon=True,
        )
        self._engine_b_thread.start()
        self._write_state({"last_action": "engine-b-start"})
        logger.info("Engine B started")
        return {"status": "started", "queue_depth": 0}

    def stop_engine_b(self) -> dict[str, Any]:
        """Stop the Engine B intake worker."""
        if self._engine_b_thread is None or not self._engine_b_thread.is_alive():
            return {"status": "not_running"}

        self._engine_b_stop_event.set()
        self._engine_b_queue.put(None)
        self._engine_b_thread.join(timeout=15)
        self._engine_b_thread = None
        self._engine_b_pipeline = None
        self._engine_b_queue = queue.Queue()
        self._write_state({"last_action": "engine-b-stop"})
        logger.info("Engine B stopped")
        return {"status": "stopped"}

    def submit_engine_b_event(
        self,
        *,
        raw_content: str,
        source_class: str,
        source_credibility: float,
        source_ids: list[str],
        job_id: str | None = None,
        on_success: Optional[Callable[[dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        allow_ad_hoc: bool = True,
    ) -> dict[str, Any]:
        """Submit Engine B work to the managed queue or an ad hoc worker."""
        if self._engine_b_factory is None:
            return {"status": "unavailable", "detail": "engine_b_factory not configured"}

        job = {
            "job_id": job_id,
            "raw_content": raw_content,
            "source_class": source_class,
            "source_credibility": source_credibility,
            "source_ids": list(source_ids),
            "on_success": on_success,
            "on_error": on_error,
        }

        running = self._engine_b_thread is not None and self._engine_b_thread.is_alive()
        if running:
            self._engine_b_queue.put(job)
            return {
                "status": "queued",
                "job_id": job_id,
                "queue_depth": self._engine_b_queue.qsize(),
            }

        if config.ENGINE_B_ENABLED:
            started = self.start_engine_b()
            if started.get("status") not in {"started", "already_running"}:
                return started
            self._engine_b_queue.put(job)
            return {
                "status": "queued",
                "job_id": job_id,
                "queue_depth": self._engine_b_queue.qsize(),
            }

        if not allow_ad_hoc:
            return {"status": "not_running", "detail": "engine_b worker is not running"}

        try:
            pipeline = self._engine_b_factory()
        except Exception as exc:
            logger.warning("Engine B pipeline bootstrap failed: %s", exc)
            return {"status": "error", "detail": str(exc)}

        thread = threading.Thread(
            target=self._run_engine_b_job,
            args=(job, pipeline),
            name=f"engine-b-ad-hoc-{(job_id or 'manual')[:8]}",
            daemon=True,
        )
        thread.start()
        return {"status": "queued", "job_id": job_id, "queue_depth": 0}

    def engine_b_status(self) -> dict[str, Any]:
        """Return Engine B service state."""
        running = self._engine_b_thread is not None and self._engine_b_thread.is_alive()
        return {
            "running": running,
            "enabled": config.ENGINE_B_ENABLED,
            "configured": self._engine_b_factory is not None,
            "queue_depth": self._engine_b_queue.qsize() if running else 0,
            "last_result": self._last_engine_b_result,
        }

    def decay_review_status(self) -> dict[str, Any]:
        return {
            "configured": self._decay_review_factory is not None,
            "last_result": self._last_decay_review_result,
        }

    def kill_check_status(self) -> dict[str, Any]:
        return {
            "configured": self._kill_monitor_factory is not None,
            "last_result": self._last_kill_check_result,
        }

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
            "engine_a": self.engine_a_status(),
            "engine_b": self.engine_b_status(),
            "decay_review": self.decay_review_status(),
            "kill_check": self.kill_check_status(),
            "intraday": self.intraday_status(),
            "last_dag_result": self._last_dag_result,
            "config": {
                "orchestrator_enabled": config.ORCHESTRATOR_ENABLED,
                "orchestrator_dry_run": config.ORCHESTRATOR_DRY_RUN,
                "ai_panel_enabled": config.AI_PANEL_ENABLED,
                "dispatcher_enabled": config.DISPATCHER_ENABLED,
                "engine_a_enabled": config.ENGINE_A_ENABLED,
                "engine_b_enabled": config.ENGINE_B_ENABLED,
                "engine_a_interval_seconds": config.ENGINE_A_INTERVAL_SECONDS,
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

        if config.ENGINE_A_ENABLED and self._engine_a_factory is not None:
            thread_dead = (
                self._engine_a_thread is None
                or not self._engine_a_thread.is_alive()
            )
            if thread_dead and not self._engine_a_stop_event.is_set():
                try:
                    result = self.start_engine_a()
                    restarted["engine_a"] = result.get("status", "unknown")
                    logger.warning("Supervisor restarted crashed Engine A loop")
                except Exception as exc:
                    logger.error("Supervisor failed to restart Engine A loop: %s", exc)

        if config.ENGINE_B_ENABLED and self._engine_b_factory is not None:
            thread_dead = (
                self._engine_b_thread is None
                or not self._engine_b_thread.is_alive()
            )
            if thread_dead and not self._engine_b_stop_event.is_set():
                try:
                    result = self.start_engine_b()
                    restarted["engine_b"] = result.get("status", "unknown")
                    logger.warning("Supervisor restarted crashed Engine B worker")
                except Exception as exc:
                    logger.error("Supervisor failed to restart Engine B worker: %s", exc)

        # Restart intraday loop if it crashed
        if config.INTRADAY_ENABLED and self._intraday_loop is None:
            try:
                result = self.start_intraday()
                restarted["intraday"] = result.get("status", "unknown")
                logger.warning("Supervisor restarted crashed intraday loop")
            except Exception as exc:
                logger.error("Supervisor failed to restart intraday loop: %s", exc)

        return restarted

    # ─── Research scheduler window handlers ──────────────────────────────

    def _run_engine_a_window(self, window_name: str, db_path: str, dry_run: bool) -> dict[str, Any]:
        if self._engine_a_factory is None:
            return {"items_processed": 0, "skipped": 1, "error_count": 0}
        pipeline = self._engine_a_factory()
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result = pipeline.run_daily(as_of)
        executed = any(
            getattr(getattr(artifact, "artifact_type", None), "value", None) == "execution_report"
            for artifact in result.artifacts
        )
        self._last_engine_a_result = {
            "status": "ok",
            "as_of": as_of,
            "artifacts": len(result.artifacts),
            "executed": executed,
            "window": window_name,
        }
        return {
            "artifacts_created": len(result.artifacts),
            "actions_taken": 1 if executed else 0,
            "error_count": 0,
        }

    def _run_decay_review_window(self, window_name: str, db_path: str, dry_run: bool) -> dict[str, Any]:
        if self._decay_review_factory is None:
            return {"items_processed": 0, "skipped": 1, "error_count": 0}
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        service = self._decay_review_factory()
        reviews = service.run_decay_check(as_of=as_of, db_path=db_path)
        self._last_decay_review_result = {
            "status": "ok",
            "as_of": as_of,
            "pending_reviews": len(reviews),
            "window": window_name,
        }
        return {
            "artifacts_created": len(reviews),
            "pending_reviews": len(reviews),
            "error_count": 0,
        }

    def _run_kill_check_window(self, window_name: str, db_path: str, dry_run: bool) -> dict[str, Any]:
        if self._kill_monitor_factory is None:
            return {"items_processed": 0, "skipped": 1, "error_count": 0}
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        monitor = self._kill_monitor_factory()
        alerts = monitor.check_all(as_of=as_of)
        auto_kills = 0
        for alert in alerts:
            if alert.auto_kill:
                monitor.execute_kill(
                    hypothesis_id=alert.hypothesis_id,
                    trigger=alert.trigger,
                    trigger_detail=alert.trigger_detail,
                    operator_approved=False,
                )
                auto_kills += 1
        self._last_kill_check_result = {
            "status": "ok",
            "as_of": as_of,
            "alerts": len(alerts),
            "auto_kills": auto_kills,
            "window": window_name,
        }
        return {
            "items_processed": len(alerts),
            "actions_taken": auto_kills,
            "skipped": max(0, len(alerts) - auto_kills),
            "error_count": 0,
        }

    def _run_engine_b_job(self, job: dict[str, Any], pipeline: Any) -> None:
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            result = pipeline.process_event(
                raw_content=job["raw_content"],
                source_class=job["source_class"],
                source_credibility=job["source_credibility"],
                source_ids=job["source_ids"],
            )
            summary = {
                "status": "ok",
                "as_of": as_of,
                "job_id": job.get("job_id"),
                "artifact_types": [
                    getattr(
                        getattr(artifact, "artifact_type", None),
                        "value",
                        str(getattr(artifact, "artifact_type", "")),
                    )
                    for artifact in getattr(result, "artifacts", [])
                ],
                "artifact_count": len(getattr(result, "artifacts", [])),
                "outcome": getattr(getattr(result, "outcome", None), "value", None),
                "score": getattr(result, "score", None),
                "next_stage": getattr(result, "next_stage", None),
                "current_stage": getattr(result, "current_stage", None),
                "requires_human_signoff": bool(getattr(result, "requires_human_signoff", False)),
                "blocking_reasons": list(getattr(result, "blocking_reasons", [])),
            }
            self._last_engine_b_result = summary
            on_success = job.get("on_success")
            if callable(on_success):
                try:
                    on_success(summary)
                except Exception as callback_exc:
                    logger.warning("Engine B success callback error: %s", callback_exc)
        except Exception as exc:
            self._last_engine_b_result = {
                "status": "failed",
                "as_of": as_of,
                "job_id": job.get("job_id"),
                "error": str(exc),
            }
            logger.warning("Engine B job error: %s", exc)
            on_error = job.get("on_error")
            if callable(on_error):
                try:
                    on_error(exc)
                except Exception as callback_exc:
                    logger.warning("Engine B error callback failed: %s", callback_exc)
