"""Daily workflow scheduler — dispatches orchestration cycles on a time-based schedule.

C-003: Runs registered strategy slots through the orchestration engine at
configured times each trading day.  Provides a thread-safe lifecycle
(start / stop / pause / status) and persists run state for crash recovery.

The scheduler does NOT replace the existing OptionsBot timer loop; it runs
alongside it as a separate background thread dedicated to the multi-strategy
orchestrator pipeline (C-001).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from data.trade_db import (
    DB_PATH,
    create_job,
    load_strategy_state,
    log_event,
    save_strategy_state,
    update_job,
)

logger = logging.getLogger(__name__)

_REAL_DATETIME = datetime


def _utcnow_naive() -> datetime:
    """Return UTC now as naive datetime for backward-compatible ISO strings."""
    # Tests patch module-level ``datetime`` and set ``utcnow``; support both.
    current = datetime.now(timezone.utc)
    if not isinstance(current, _REAL_DATETIME):
        current = datetime.utcnow()
    if current.tzinfo is not None:
        return current.replace(tzinfo=None)
    return current


# ─── Schedule configuration ──────────────────────────────────────────────

@dataclass(frozen=True)
class ScheduleWindow:
    """One scheduled trigger within a day.

    Attributes:
        name: Human-readable label (e.g. "us_close_scan").
        hour: UTC hour to fire (0-23).
        minute: UTC minute to fire (0-59).
        weekdays: Set of ISO weekday numbers (1=Mon … 7=Sun).
                  Empty means every day.
        enabled: Master enable flag.
    """

    name: str
    hour: int
    minute: int
    weekdays: frozenset[int] = frozenset({1, 2, 3, 4, 5})  # Mon–Fri default
    enabled: bool = True

    def __post_init__(self):
        if not (0 <= self.hour <= 23):
            raise ValueError(f"hour must be 0-23, got {self.hour}")
        if not (0 <= self.minute <= 59):
            raise ValueError(f"minute must be 0-59, got {self.minute}")
        if not self.name.strip():
            raise ValueError("name is required")


# Conservative defaults — weekday-only, after US market close
DEFAULT_SCHEDULE: list[ScheduleWindow] = [
    ScheduleWindow(name="us_close_orchestration", hour=21, minute=30),
    # Full signal layer ingestion (L1-L8 batch)
    ScheduleWindow(name="tier1_full_ingest", hour=21, minute=0),
    # News sentiment refresh (L6 only, 4x daily)
    ScheduleWindow(name="news_refresh_morning", hour=8, minute=0),
    ScheduleWindow(name="news_refresh_noon", hour=12, minute=0),
    ScheduleWindow(name="news_refresh_afternoon", hour=16, minute=0),
    ScheduleWindow(name="news_refresh_evening", hour=20, minute=0),
    # Congressional trading refresh (daily, including weekends for disclosure lag)
    ScheduleWindow(name="congressional_refresh", hour=22, minute=0,
                   weekdays=frozenset({1, 2, 3, 4, 5, 6, 7})),
    # Macro regime + options sentiment (weekday evenings)
    ScheduleWindow(name="macro_regime_ingest", hour=20, minute=30),
    # CFTC Commitment of Traders (Saturday — data released Friday evening)
    ScheduleWindow(name="cot_ingest", hour=10, minute=0,
                   weekdays=frozenset({6})),
    # Fundamental quality screen (Sunday morning)
    ScheduleWindow(name="fundamentals_ingest", hour=6, minute=0,
                   weekdays=frozenset({7})),
    # Koyfin scraper (Sunday morning, after fundamentals)
    ScheduleWindow(name="koyfin_scrape", hour=7, minute=0,
                   weekdays=frozenset({7})),
    # ShareScope UK screen scraper (Sunday morning)
    ScheduleWindow(name="sharescope_scrape", hour=7, minute=30,
                   weekdays=frozenset({7})),
]


# ─── State key prefix for DB persistence ─────────────────────────────────

_STATE_KEY_PREFIX = "scheduler"


def _state_key(suffix: str) -> str:
    return f"{_STATE_KEY_PREFIX}:{suffix}"


# ─── Scheduler result ────────────────────────────────────────────────────

@dataclass
class SchedulerRunResult:
    """Result of a single scheduled orchestration dispatch."""

    window_name: str
    job_id: str
    started_at: str
    finished_at: str = ""
    success: bool = False
    signals_total: int = 0
    intents_created: int = 0
    intents_rejected: int = 0
    errors_total: int = 0
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Core scheduler ──────────────────────────────────────────────────────

class DailyWorkflowScheduler:
    """Time-based scheduler that dispatches orchestration cycles.

    Lifecycle:
        scheduler = DailyWorkflowScheduler(dispatch_fn=my_dispatch)
        scheduler.start()        # spawns daemon thread
        scheduler.pause()        # suspend dispatch (tick loop continues)
        scheduler.resume()       # resume dispatch
        scheduler.stop()         # graceful shutdown

    The ``dispatch_fn`` callback is invoked for each schedule window that
    fires.  It receives ``(window_name: str, db_path: str, dry_run: bool)``
    and must return an ``OrchestrationResult`` (or compatible object with a
    ``.summary()`` method).

    Run history and last-fired timestamps are persisted to the strategy_state
    table so the scheduler can recover after a restart without re-firing
    windows that already ran today.
    """

    def __init__(
        self,
        dispatch_fn: Callable[..., Any],
        schedule: Optional[list[ScheduleWindow]] = None,
        db_path: str = DB_PATH,
        dry_run: bool = False,
        tick_interval: float = 30.0,
        rebalance_check_fn: Optional[Callable[..., Any]] = None,
    ):
        self._dispatch_fn = dispatch_fn
        self._schedule = list(schedule if schedule is not None else DEFAULT_SCHEDULE)
        self._db_path = db_path
        self._dry_run = dry_run
        self._tick_interval = max(5.0, tick_interval)
        self._rebalance_check_fn = rebalance_check_fn

        # Thread-safe state
        self._lock = threading.RLock()
        self._running = False
        self._paused = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[str] = None

        # Deduplication: window names already fired today
        self._fired_today: set[str] = set()
        self._today: Optional[date] = None

        # Recent run results (bounded ring buffer)
        self._recent_results: list[SchedulerRunResult] = []
        self._max_recent = 50

    # ── Public lifecycle ──────────────────────────────────────────────────

    def start(self) -> dict[str, Any]:
        """Start the scheduler loop in a daemon thread."""
        with self._lock:
            if self._running:
                return {"ok": False, "message": "Scheduler already running."}

            self._stop_event.clear()
            self._running = True
            self._paused = False
            self._started_at = _utcnow_naive().isoformat()

            # Recover last-fired state from DB
            self._recover_state()

            self._thread = threading.Thread(
                target=self._run_loop,
                name="daily-workflow-scheduler",
                daemon=True,
            )
            self._thread.start()

        log_event(
            category="SCHEDULE",
            headline="Daily workflow scheduler started",
            detail=f"windows={len(self._schedule)}, dry_run={self._dry_run}",
            db_path=self._db_path,
        )
        logger.info("Scheduler started with %d windows", len(self._schedule))
        return {"ok": True, "message": "Scheduler started."}

    def stop(self, timeout: float = 15.0) -> dict[str, Any]:
        """Request graceful stop and wait for the thread to exit."""
        with self._lock:
            if not self._running:
                return {"ok": False, "message": "Scheduler is not running."}
            self._stop_event.set()
            thread = self._thread

        if thread is not None:
            thread.join(timeout=timeout)

        with self._lock:
            still_alive = thread is not None and thread.is_alive()
            if not still_alive:
                self._running = False
                self._thread = None
                self._started_at = None

        if still_alive:
            return {"ok": False, "message": "Scheduler did not stop within timeout."}

        log_event(
            category="SCHEDULE",
            headline="Daily workflow scheduler stopped",
            db_path=self._db_path,
        )
        logger.info("Scheduler stopped")
        return {"ok": True, "message": "Scheduler stopped."}

    def pause(self) -> dict[str, Any]:
        """Pause dispatch (tick loop continues but windows are skipped)."""
        with self._lock:
            if not self._running:
                return {"ok": False, "message": "Scheduler is not running."}
            self._paused = True
        logger.info("Scheduler paused")
        return {"ok": True, "message": "Scheduler paused."}

    def resume(self) -> dict[str, Any]:
        """Resume dispatch after a pause."""
        with self._lock:
            if not self._running:
                return {"ok": False, "message": "Scheduler is not running."}
            self._paused = False
        logger.info("Scheduler resumed")
        return {"ok": True, "message": "Scheduler resumed."}

    def trigger_now(self, window_name: str) -> dict[str, Any]:
        """Manually trigger a named window immediately, bypassing time checks.

        This ignores deduplication and weekday filters — useful for operator
        testing and manual overrides.
        """
        window = self._find_window(window_name)
        if window is None:
            return {
                "ok": False,
                "message": f"Window '{window_name}' not found in schedule.",
            }
        result = self._dispatch_window(window)
        return {
            "ok": result.success,
            "message": f"Manual trigger complete: {result.signals_total} signals, "
                       f"{result.intents_created} intents",
            "result": result.to_dict(),
        }

    def status(self) -> dict[str, Any]:
        """Return current scheduler state."""
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "started_at": self._started_at,
                "dry_run": self._dry_run,
                "schedule": [
                    {
                        "name": w.name,
                        "hour": w.hour,
                        "minute": w.minute,
                        "weekdays": sorted(w.weekdays),
                        "enabled": w.enabled,
                    }
                    for w in self._schedule
                ],
                "fired_today": sorted(self._fired_today),
                "recent_results": [r.to_dict() for r in self._recent_results[-5:]],
            }

    # ── Schedule management ───────────────────────────────────────────────

    def add_window(self, window: ScheduleWindow) -> None:
        """Add a schedule window (thread-safe)."""
        with self._lock:
            # Replace if same name exists
            self._schedule = [w for w in self._schedule if w.name != window.name]
            self._schedule.append(window)
        logger.info("Added schedule window: %s at %02d:%02d UTC",
                     window.name, window.hour, window.minute)

    def remove_window(self, name: str) -> bool:
        """Remove a schedule window by name. Returns True if found."""
        with self._lock:
            before = len(self._schedule)
            self._schedule = [w for w in self._schedule if w.name != name]
            return len(self._schedule) < before

    # ── Internal loop ─────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main tick loop — runs until stop is requested."""
        logger.debug("Scheduler loop started")
        try:
            while not self._stop_event.is_set():
                self._tick()
                self._stop_event.wait(timeout=self._tick_interval)
        except Exception as exc:
            logger.error("Scheduler loop crashed: %s", exc, exc_info=True)
            log_event(
                category="ERROR",
                headline="Scheduler loop crashed",
                detail=str(exc),
                db_path=self._db_path,
            )
        finally:
            with self._lock:
                self._running = False
                self._thread = None
                self._started_at = None
            logger.debug("Scheduler loop exited")

    def _tick(self) -> None:
        """One tick: check time, reset daily state, fire due windows."""
        now = _utcnow_naive()
        today = now.date()

        # Day rollover — clear fired set
        with self._lock:
            if self._today != today:
                self._fired_today.clear()
                self._today = today
                logger.debug("Day rollover to %s", today.isoformat())

            if self._paused:
                return

            schedule_snapshot = list(self._schedule)
            fired_snapshot = set(self._fired_today)

        # Check each window
        for window in schedule_snapshot:
            if not window.enabled:
                continue
            if window.name in fired_snapshot:
                continue
            if window.weekdays and now.isoweekday() not in window.weekdays:
                continue
            if now.hour != window.hour or now.minute != window.minute:
                continue

            # Window is due — dispatch
            self._dispatch_window(window)

            with self._lock:
                self._fired_today.add(window.name)

            # Persist fired state for crash recovery
            self._persist_state()

    def _dispatch_window(self, window: ScheduleWindow) -> SchedulerRunResult:
        """Run the dispatch function for one schedule window.

        The entire body is wrapped in try/except so that transient DB failures
        (e.g. SQLite lock on create_job / log_event) cannot crash the scheduler
        thread — they are captured in the result and logged.
        """
        job_id = uuid.uuid4().hex[:12]
        started_at = _utcnow_naive().isoformat()
        result = SchedulerRunResult(
            window_name=window.name,
            job_id=job_id,
            started_at=started_at,
        )

        try:
            logger.info(
                "Dispatching window '%s' — job %s (dry_run=%s)",
                window.name, job_id, self._dry_run,
            )

            # Create job record
            create_job(
                job_id=job_id,
                job_type="orchestration_cycle",
                status="running",
                mode="shadow" if self._dry_run else "live",
                detail=f"Scheduled: {window.name}",
                db_path=self._db_path,
            )

            log_event(
                category="SCHEDULE",
                headline=f"Orchestration dispatch: {window.name}",
                detail=f"job_id={job_id}, dry_run={self._dry_run}",
                db_path=self._db_path,
            )

            orch_result = self._dispatch_fn(
                window_name=window.name,
                db_path=self._db_path,
                dry_run=self._dry_run,
            )

            # Extract summary from OrchestrationResult
            summary = (
                orch_result.summary()
                if hasattr(orch_result, "summary")
                else orch_result
            )
            result.success = True
            result.signals_total = summary.get("signals_total", 0)
            result.intents_created = summary.get("intents_created", 0)
            result.intents_rejected = summary.get("intents_rejected", 0)
            result.errors_total = summary.get("errors", 0)

            update_job(
                job_id=job_id,
                status="completed",
                result=json.dumps(summary),
                db_path=self._db_path,
            )
            log_event(
                category="SCHEDULE",
                headline=f"Orchestration complete: {window.name}",
                detail=(
                    f"signals={result.signals_total}, "
                    f"intents={result.intents_created}, "
                    f"rejected={result.intents_rejected}, "
                    f"errors={result.errors_total}"
                ),
                db_path=self._db_path,
            )
            logger.info(
                "Window '%s' complete: %d signals, %d intents, %d rejected, %d errors",
                window.name, result.signals_total, result.intents_created,
                result.intents_rejected, result.errors_total,
            )
            self._run_rebalance_check(window.name)

        except Exception as exc:
            result.error_message = str(exc)
            # Best-effort DB updates — if these also fail, just log
            try:
                update_job(
                    job_id=job_id,
                    status="failed",
                    error=str(exc),
                    db_path=self._db_path,
                )
            except Exception:
                logger.warning("Could not update job %s to failed", job_id)
            try:
                log_event(
                    category="ERROR",
                    headline=f"Orchestration failed: {window.name}",
                    detail=str(exc),
                    db_path=self._db_path,
                )
            except Exception:
                logger.warning("Could not log error event for %s", window.name)
            logger.error(
                "Window '%s' dispatch failed: %s", window.name, exc, exc_info=True,
            )

        result.finished_at = _utcnow_naive().isoformat()

        # Store in recent results ring buffer
        with self._lock:
            self._recent_results.append(result)
            if len(self._recent_results) > self._max_recent:
                self._recent_results = self._recent_results[-self._max_recent:]

        return result

    # ── State persistence for crash recovery ──────────────────────────────

    def _persist_state(self) -> None:
        """Save current fired-today set to DB for recovery."""
        with self._lock:
            state = {
                "date": self._today.isoformat() if self._today else None,
                "fired": sorted(self._fired_today),
            }
        try:
            save_strategy_state(
                _state_key("fired_today"),
                json.dumps(state),
                db_path=self._db_path,
            )
        except Exception as exc:
            logger.warning("Failed to persist scheduler state: %s", exc)

    def _recover_state(self) -> None:
        """Restore fired-today set from DB if it matches today's date."""
        try:
            raw = load_strategy_state(
                _state_key("fired_today"),
                db_path=self._db_path,
            )
            if raw is None:
                return
            state = json.loads(raw)
            saved_date = state.get("date")
            utc_today = _utcnow_naive().date()
            if saved_date == utc_today.isoformat():
                self._fired_today = set(state.get("fired", []))
                self._today = utc_today
                logger.info(
                    "Recovered scheduler state: %d windows already fired today",
                    len(self._fired_today),
                )
            else:
                logger.debug(
                    "Scheduler state is from %s, not today — starting fresh",
                    saved_date,
                )
        except Exception as exc:
            logger.warning("Failed to recover scheduler state: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _find_window(self, name: str) -> Optional[ScheduleWindow]:
        with self._lock:
            for w in self._schedule:
                if w.name == name:
                    return w
        return None

    def _run_rebalance_check(self, window_name: str) -> None:
        """Best-effort hook for post-dispatch sleeve drift evaluation."""
        if self._rebalance_check_fn is None:
            return

        try:
            rebalance_result = self._rebalance_check_fn(
                window_name=window_name,
                db_path=self._db_path,
            )

            triggered = False
            if isinstance(rebalance_result, dict):
                triggered = bool(rebalance_result.get("requires_rebalance", False))

            log_event(
                category="SCHEDULE",
                headline=f"Rebalance check: {window_name}",
                detail=f"triggered={triggered}",
                db_path=self._db_path,
            )
            logger.info(
                "Rebalance check complete for '%s' (triggered=%s)",
                window_name,
                triggered,
            )
        except Exception as exc:
            logger.warning(
                "Rebalance check failed for '%s': %s",
                window_name,
                exc,
            )
