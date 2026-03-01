"""Tests for C-003 — DailyWorkflowScheduler.

Covers:
  - ScheduleWindow validation
  - Scheduler lifecycle (start/stop/pause/resume)
  - Tick dispatch logic (time matching, weekday filtering, deduplication)
  - Day rollover (fired set clears)
  - Dispatch success and failure paths
  - Manual trigger_now (including unknown window)
  - State persistence and crash recovery
  - Schedule management (add/remove windows)
  - Status reporting
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from data.trade_db import init_db, load_strategy_state, save_strategy_state

# Import the module under test
from app.engine.scheduler import (
    DEFAULT_SCHEDULE,
    DailyWorkflowScheduler,
    ScheduleWindow,
    SchedulerRunResult,
    _state_key,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Create a temporary SQLite database for each test."""
    db_file = str(tmp_path / "test_scheduler.db")
    init_db(db_file)
    return db_file


class FakeOrchestrationResult:
    """Mimics OrchestrationResult.summary() for dispatch tests."""

    def __init__(
        self,
        signals_total: int = 3,
        intents_created: int = 2,
        intents_rejected: int = 0,
        errors: int = 0,
    ):
        self._summary = {
            "run_id": "test123",
            "run_at": "2026-03-01T21:30:00",
            "signals_total": signals_total,
            "intents_created": intents_created,
            "intents_rejected": intents_rejected,
            "errors": errors,
        }

    def summary(self) -> dict[str, Any]:
        return self._summary


def make_dispatch_fn(
    return_value: Any = None,
    side_effect: Exception | None = None,
):
    """Create a mock dispatch function with optional failure."""
    fn = MagicMock()
    if side_effect:
        fn.side_effect = side_effect
    else:
        fn.return_value = return_value or FakeOrchestrationResult()
    return fn


# ─── ScheduleWindow validation ───────────────────────────────────────────

class TestScheduleWindow:
    def test_valid_window(self):
        w = ScheduleWindow(name="test", hour=21, minute=30)
        assert w.name == "test"
        assert w.hour == 21
        assert w.minute == 30
        assert w.weekdays == frozenset({1, 2, 3, 4, 5})
        assert w.enabled is True

    def test_custom_weekdays(self):
        w = ScheduleWindow(
            name="weekend", hour=10, minute=0,
            weekdays=frozenset({6, 7}),
        )
        assert w.weekdays == frozenset({6, 7})

    def test_invalid_hour_too_high(self):
        with pytest.raises(ValueError, match="hour must be 0-23"):
            ScheduleWindow(name="bad", hour=24, minute=0)

    def test_invalid_hour_negative(self):
        with pytest.raises(ValueError, match="hour must be 0-23"):
            ScheduleWindow(name="bad", hour=-1, minute=0)

    def test_invalid_minute_too_high(self):
        with pytest.raises(ValueError, match="minute must be 0-59"):
            ScheduleWindow(name="bad", hour=12, minute=60)

    def test_empty_name(self):
        with pytest.raises(ValueError, match="name is required"):
            ScheduleWindow(name="", hour=12, minute=0)

    def test_whitespace_name(self):
        with pytest.raises(ValueError, match="name is required"):
            ScheduleWindow(name="   ", hour=12, minute=0)

    def test_frozen(self):
        w = ScheduleWindow(name="test", hour=12, minute=0)
        with pytest.raises(AttributeError):
            w.hour = 14  # type: ignore[misc]


# ─── Scheduler lifecycle ─────────────────────────────────────────────────

class TestSchedulerLifecycle:
    def test_start_and_stop(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        result = sched.start()
        assert result["ok"] is True
        assert sched.status()["running"] is True

        result = sched.stop()
        assert result["ok"] is True
        assert sched.status()["running"] is False

    def test_double_start_rejected(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched.start()
        try:
            result = sched.start()
            assert result["ok"] is False
            assert "already running" in result["message"].lower()
        finally:
            sched.stop()

    def test_stop_when_not_running(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        result = sched.stop()
        assert result["ok"] is False
        assert "not running" in result["message"].lower()

    def test_pause_and_resume(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched.start()
        try:
            # Pause
            result = sched.pause()
            assert result["ok"] is True
            assert sched.status()["paused"] is True

            # Resume
            result = sched.resume()
            assert result["ok"] is True
            assert sched.status()["paused"] is False
        finally:
            sched.stop()

    def test_pause_when_not_running(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        result = sched.pause()
        assert result["ok"] is False

    def test_resume_when_not_running(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        result = sched.resume()
        assert result["ok"] is False

    def test_daemon_thread(self, db):
        """Scheduler thread must be daemon so it doesn't prevent process exit."""
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched.start()
        try:
            assert sched._thread is not None
            assert sched._thread.daemon is True
            assert sched._thread.name == "daily-workflow-scheduler"
        finally:
            sched.stop()


# ─── Tick dispatch logic ─────────────────────────────────────────────────

class TestTickLogic:
    def test_window_fires_at_matching_time(self, db):
        """Window fires when hour and minute match."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(
            name="test_scan", hour=14, minute=30,
            weekdays=frozenset(),  # any day
        )
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        # Simulate tick at 14:30 UTC
        fake_now = datetime(2026, 3, 2, 14, 30, 15)  # Monday
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        dispatch.assert_called_once()
        assert "test_scan" in sched._fired_today

    def test_window_does_not_fire_at_wrong_time(self, db):
        """Window does NOT fire when time doesn't match."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        fake_now = datetime(2026, 3, 2, 14, 29, 55)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        dispatch.assert_not_called()

    def test_window_skipped_on_wrong_weekday(self, db):
        """Window skipped when today is not in weekdays set."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(
            name="test", hour=14, minute=30,
            weekdays=frozenset({1}),  # Monday only
        )
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        # 2026-03-03 is a Tuesday (isoweekday=2)
        fake_now = datetime(2026, 3, 3, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        dispatch.assert_not_called()

    def test_window_fires_on_correct_weekday(self, db):
        """Window fires when today is in weekdays set."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(
            name="test", hour=14, minute=30,
            weekdays=frozenset({2}),  # Tuesday only
        )
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        # 2026-03-03 is Tuesday (isoweekday=2)
        fake_now = datetime(2026, 3, 3, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        dispatch.assert_called_once()

    def test_disabled_window_skipped(self, db):
        """Disabled windows are never fired."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(
            name="test", hour=14, minute=30,
            weekdays=frozenset(), enabled=False,
        )
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        fake_now = datetime(2026, 3, 2, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        dispatch.assert_not_called()

    def test_already_fired_window_not_repeated(self, db):
        """Once a window fires today, it does not fire again."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        fake_now = datetime(2026, 3, 2, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()

            # First tick — fires
            sched._tick()
            assert dispatch.call_count == 1

            # Second tick — deduplication prevents re-fire
            sched._tick()
            assert dispatch.call_count == 1

    def test_paused_scheduler_skips_dispatch(self, db):
        """Paused scheduler ticks but does not dispatch."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )
        sched._paused = True

        fake_now = datetime(2026, 3, 2, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        dispatch.assert_not_called()


# ─── Day rollover ─────────────────────────────────────────────────────────

class TestDayRollover:
    def test_fired_set_clears_on_new_day(self, db):
        """Fired set clears when the date rolls over."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        # Day 1: fire at 14:30
        day1 = datetime(2026, 3, 2, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = day1
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._tick()

        assert "test" in sched._fired_today
        assert dispatch.call_count == 1

        # Day 2: same time — should fire again after rollover
        day2 = datetime(2026, 3, 3, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = day2
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._tick()

        assert dispatch.call_count == 2


# ─── Dispatch success and failure ─────────────────────────────────────────

class TestDispatch:
    def test_success_dispatch_records_result(self, db):
        """Successful dispatch records signals/intents counts."""
        orch_result = FakeOrchestrationResult(
            signals_total=5, intents_created=3,
            intents_rejected=1, errors=0,
        )
        dispatch = make_dispatch_fn(return_value=orch_result)
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        result = sched._dispatch_window(window)

        assert result.success is True
        assert result.signals_total == 5
        assert result.intents_created == 3
        assert result.intents_rejected == 1
        assert result.errors_total == 0
        assert result.window_name == "test"
        assert result.error_message == ""
        assert result.finished_at != ""

    def test_failure_dispatch_captures_error(self, db):
        """Failed dispatch captures exception message."""
        dispatch = make_dispatch_fn(side_effect=RuntimeError("Broker offline"))
        window = ScheduleWindow(name="fail_test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        result = sched._dispatch_window(window)

        assert result.success is False
        assert "Broker offline" in result.error_message
        assert result.signals_total == 0

    def test_dispatch_passes_dry_run_flag(self, db):
        """Dispatch function receives the dry_run flag."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, dry_run=True, tick_interval=5.0,
        )

        sched._dispatch_window(window)

        dispatch.assert_called_once_with(
            window_name="test",
            db_path=db,
            dry_run=True,
        )

    def test_dispatch_creates_and_updates_job(self, db):
        """Dispatch creates a job record and updates it on completion."""
        from data.trade_db import get_jobs

        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        sched._dispatch_window(window)

        jobs = get_jobs(db_path=db)
        assert len(jobs) >= 1
        job = jobs[0]
        assert job["job_type"] == "orchestration_cycle"
        assert job["status"] == "completed"
        assert job["result"] is not None

    def test_failed_dispatch_records_job_error(self, db):
        """Failed dispatch sets job status to 'failed' with error message."""
        from data.trade_db import get_jobs

        dispatch = make_dispatch_fn(side_effect=ValueError("Bad data"))
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        sched._dispatch_window(window)

        jobs = get_jobs(db_path=db)
        job = jobs[0]
        assert job["status"] == "failed"
        assert "Bad data" in job["error"]

    def test_dispatch_result_stored_in_ring_buffer(self, db):
        """Results accumulate in the ring buffer up to max_recent."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )
        sched._max_recent = 3

        for _ in range(5):
            sched._dispatch_window(window)

        assert len(sched._recent_results) == 3

    def test_dispatch_with_dict_return(self, db):
        """Dispatch handles return values that are plain dicts (no .summary())."""
        raw_dict = {
            "signals_total": 2,
            "intents_created": 1,
            "intents_rejected": 0,
            "errors": 0,
        }
        dispatch = make_dispatch_fn(return_value=raw_dict)
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        result = sched._dispatch_window(window)
        assert result.success is True
        assert result.signals_total == 2
        assert result.intents_created == 1


# ─── Manual trigger ───────────────────────────────────────────────────────

class TestTriggerNow:
    def test_trigger_known_window(self, db):
        """Manual trigger fires the named window immediately."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="manual_test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        result = sched.trigger_now("manual_test")
        assert result["ok"] is True
        dispatch.assert_called_once()

    def test_trigger_unknown_window(self, db):
        """Manual trigger with unknown name returns error."""
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )

        result = sched.trigger_now("nonexistent")
        assert result["ok"] is False
        assert "not found" in result["message"].lower()
        dispatch.assert_not_called()

    def test_trigger_bypasses_deduplication(self, db):
        """Manual trigger ignores the fired-today set."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        # Mark as already fired
        sched._fired_today.add("test")

        # Manual trigger still fires
        result = sched.trigger_now("test")
        assert result["ok"] is True
        dispatch.assert_called_once()


# ─── State persistence / crash recovery ───────────────────────────────────

class TestStatePersistence:
    def test_persist_and_recover_same_day(self, db):
        """State round-trips through DB when same day (UTC)."""
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )

        utc_today = datetime.utcnow().date()
        sched._today = utc_today
        sched._fired_today = {"window_a", "window_b"}
        sched._persist_state()

        # Create a new scheduler and recover
        sched2 = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched2._recover_state()

        assert sched2._fired_today == {"window_a", "window_b"}
        assert sched2._today == utc_today

    def test_recover_uses_utc_not_local(self, db):
        """P1 regression: recovery must compare against UTC date, not local."""
        dispatch = make_dispatch_fn()

        # Persist state with today's UTC date
        utc_today = datetime.utcnow().date()
        state = json.dumps({
            "date": utc_today.isoformat(),
            "fired": ["utc_window"],
        })
        save_strategy_state(_state_key("fired_today"), state, db_path=db)

        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched._recover_state()

        # Must match since we used UTC date for both persist and recover
        assert sched._fired_today == {"utc_window"}
        assert sched._today == utc_today

    def test_stale_state_ignored(self, db):
        """State from a previous day is not loaded."""
        dispatch = make_dispatch_fn()
        # Manually save state with yesterday's date
        from datetime import timedelta
        yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
        state = json.dumps({"date": yesterday, "fired": ["old_window"]})
        save_strategy_state(_state_key("fired_today"), state, db_path=db)

        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched._recover_state()

        assert len(sched._fired_today) == 0

    def test_corrupt_state_handled_gracefully(self, db):
        """Corrupt state in DB does not crash recovery."""
        save_strategy_state(
            _state_key("fired_today"), "not valid json{{{",
            db_path=db,
        )

        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        # Should not raise
        sched._recover_state()
        assert len(sched._fired_today) == 0

    def test_missing_state_handled_gracefully(self, db):
        """No prior state in DB is handled cleanly."""
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched._recover_state()
        assert len(sched._fired_today) == 0


# ─── Schedule management ─────────────────────────────────────────────────

class TestScheduleManagement:
    def test_add_window(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[], db_path=db, tick_interval=5.0,
        )

        new_window = ScheduleWindow(name="new", hour=10, minute=0, weekdays=frozenset())
        sched.add_window(new_window)

        assert len(sched._schedule) == 1
        assert sched._schedule[0].name == "new"

    def test_add_window_replaces_existing(self, db):
        """Adding a window with the same name replaces the old one."""
        dispatch = make_dispatch_fn()
        original = ScheduleWindow(name="scan", hour=10, minute=0, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[original], db_path=db, tick_interval=5.0,
        )

        replacement = ScheduleWindow(name="scan", hour=15, minute=45, weekdays=frozenset())
        sched.add_window(replacement)

        assert len(sched._schedule) == 1
        assert sched._schedule[0].hour == 15
        assert sched._schedule[0].minute == 45

    def test_remove_window(self, db):
        dispatch = make_dispatch_fn()
        w1 = ScheduleWindow(name="a", hour=10, minute=0, weekdays=frozenset())
        w2 = ScheduleWindow(name="b", hour=11, minute=0, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[w1, w2], db_path=db, tick_interval=5.0,
        )

        removed = sched.remove_window("a")
        assert removed is True
        assert len(sched._schedule) == 1
        assert sched._schedule[0].name == "b"

    def test_remove_nonexistent_window(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[], db_path=db, tick_interval=5.0,
        )
        removed = sched.remove_window("ghost")
        assert removed is False


# ─── Status reporting ─────────────────────────────────────────────────────

class TestStatus:
    def test_status_when_idle(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        status = sched.status()
        assert status["running"] is False
        assert status["paused"] is False
        assert status["started_at"] is None
        assert isinstance(status["schedule"], list)
        assert isinstance(status["fired_today"], list)
        assert isinstance(status["recent_results"], list)

    def test_status_when_running(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, tick_interval=5.0,
        )
        sched.start()
        try:
            status = sched.status()
            assert status["running"] is True
            assert status["started_at"] is not None
        finally:
            sched.stop()

    def test_status_schedule_details(self, db):
        dispatch = make_dispatch_fn()
        windows = [
            ScheduleWindow(name="a", hour=9, minute=30, weekdays=frozenset({1, 2, 3})),
            ScheduleWindow(name="b", hour=21, minute=0, enabled=False),
        ]
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=windows,
            db_path=db, tick_interval=5.0,
        )
        status = sched.status()
        assert len(status["schedule"]) == 2

        a = status["schedule"][0]
        assert a["name"] == "a"
        assert a["hour"] == 9
        assert a["minute"] == 30
        assert a["weekdays"] == [1, 2, 3]
        assert a["enabled"] is True

        b = status["schedule"][1]
        assert b["enabled"] is False

    def test_status_dry_run_flag(self, db):
        dispatch = make_dispatch_fn()
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, db_path=db, dry_run=True, tick_interval=5.0,
        )
        assert sched.status()["dry_run"] is True


# ─── SchedulerRunResult ───────────────────────────────────────────────────

class TestSchedulerRunResult:
    def test_to_dict_round_trip(self):
        r = SchedulerRunResult(
            window_name="test",
            job_id="abc123",
            started_at="2026-03-01T21:30:00",
            finished_at="2026-03-01T21:30:05",
            success=True,
            signals_total=3,
            intents_created=2,
            intents_rejected=1,
            errors_total=0,
        )
        d = r.to_dict()
        assert d["window_name"] == "test"
        assert d["success"] is True
        assert d["signals_total"] == 3
        assert d["intents_created"] == 2
        assert d["intents_rejected"] == 1


# ─── Default schedule ─────────────────────────────────────────────────────

class TestDefaultSchedule:
    def test_default_schedule_exists(self):
        assert len(DEFAULT_SCHEDULE) >= 1

    def test_default_schedule_weekday_only(self):
        for window in DEFAULT_SCHEDULE:
            assert window.weekdays == frozenset({1, 2, 3, 4, 5})

    def test_default_schedule_enabled(self):
        for window in DEFAULT_SCHEDULE:
            assert window.enabled is True


# ─── Multiple windows in one tick ─────────────────────────────────────────

class TestMultipleWindows:
    def test_two_windows_same_time_both_fire(self, db):
        """Two windows scheduled at the same time should both fire."""
        dispatch = make_dispatch_fn()
        w1 = ScheduleWindow(name="scan_a", hour=14, minute=30, weekdays=frozenset())
        w2 = ScheduleWindow(name="scan_b", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[w1, w2],
            db_path=db, tick_interval=5.0,
        )

        fake_now = datetime(2026, 3, 2, 14, 30, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        assert dispatch.call_count == 2
        assert sched._fired_today == {"scan_a", "scan_b"}

    def test_two_windows_different_times(self, db):
        """Only the matching window fires."""
        dispatch = make_dispatch_fn()
        w1 = ScheduleWindow(name="early", hour=9, minute=0, weekdays=frozenset())
        w2 = ScheduleWindow(name="late", hour=21, minute=0, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[w1, w2],
            db_path=db, tick_interval=5.0,
        )

        fake_now = datetime(2026, 3, 2, 9, 0, 0)
        with patch("app.engine.scheduler.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            sched._today = fake_now.date()
            sched._tick()

        assert dispatch.call_count == 1
        assert "early" in sched._fired_today
        assert "late" not in sched._fired_today


# ─── Event logging ────────────────────────────────────────────────────────

class TestEventLogging:
    def test_dispatch_logs_events(self, db):
        """Dispatch logs start and completion events."""
        from data.trade_db import get_conn

        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        sched._dispatch_window(window)

        conn = get_conn(db)
        events = conn.execute(
            "SELECT * FROM bot_events WHERE category = 'SCHEDULE' ORDER BY id"
        ).fetchall()
        conn.close()

        # Should have dispatch start + completion events
        assert len(events) >= 2
        headlines = [e["headline"] for e in events]
        assert any("dispatch" in h.lower() for h in headlines)
        assert any("complete" in h.lower() for h in headlines)

    def test_failed_dispatch_logs_error_event(self, db):
        """Failed dispatch logs an ERROR event."""
        from data.trade_db import get_conn

        dispatch = make_dispatch_fn(side_effect=RuntimeError("boom"))
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        sched._dispatch_window(window)

        conn = get_conn(db)
        events = conn.execute(
            "SELECT * FROM bot_events WHERE category = 'ERROR'"
        ).fetchall()
        conn.close()

        assert len(events) >= 1
        assert "boom" in events[0]["detail"]


# ─── P1 regression: DB failure resilience ─────────────────────────────────

class TestDBFailureResilience:
    def test_create_job_failure_does_not_crash_dispatch(self, db):
        """P1 regression: transient DB failure in create_job must not kill
        the scheduler thread — _dispatch_window must catch it."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        with patch("app.engine.scheduler.create_job", side_effect=OSError("DB locked")):
            # Must NOT raise — should be captured in result
            result = sched._dispatch_window(window)

        assert result.success is False
        assert "DB locked" in result.error_message
        # Dispatch fn should NOT have been called since create_job failed first
        dispatch.assert_not_called()

    def test_log_event_failure_does_not_crash_dispatch(self, db):
        """P1 regression: transient failure in pre-dispatch log_event must
        not crash the scheduler thread."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        call_count = 0

        def failing_log_event(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Fail on the first call (pre-dispatch), succeed on subsequent
            if call_count == 1:
                raise OSError("DB locked")

        with patch("app.engine.scheduler.log_event", side_effect=failing_log_event):
            result = sched._dispatch_window(window)

        assert result.success is False
        assert "DB locked" in result.error_message

    def test_dispatch_survives_and_thread_continues(self, db):
        """P1 regression: after a DB failure in one dispatch, the scheduler
        loop must continue ticking — it must not exit permanently."""
        dispatch = make_dispatch_fn()
        window = ScheduleWindow(name="test", hour=14, minute=30, weekdays=frozenset())
        sched = DailyWorkflowScheduler(
            dispatch_fn=dispatch, schedule=[window],
            db_path=db, tick_interval=5.0,
        )

        # First dispatch fails due to create_job error
        with patch("app.engine.scheduler.create_job", side_effect=OSError("locked")):
            result1 = sched._dispatch_window(window)
        assert result1.success is False

        # Second dispatch succeeds — proves the scheduler is still alive
        result2 = sched._dispatch_window(window)
        assert result2.success is True
