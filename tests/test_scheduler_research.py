from unittest.mock import MagicMock

import pytest

from app.engine.scheduler import DailyWorkflowScheduler, ScheduleWindow
from data.trade_db import init_db
from tests.test_scheduler import make_dispatch_fn


@pytest.fixture
def db(tmp_path):
    db_file = str(tmp_path / "test_scheduler_research.db")
    init_db(db_file)
    return db_file


def test_window_handler_overrides_default_dispatch(db):
    default_dispatch = make_dispatch_fn()
    research_handler = MagicMock(
        return_value={
            "artifacts_created": 4,
            "pending_reviews": 2,
            "skipped": 1,
            "error_count": 0,
        }
    )
    window = ScheduleWindow(name="research_decay", hour=14, minute=30, weekdays=frozenset())
    sched = DailyWorkflowScheduler(
        dispatch_fn=default_dispatch,
        schedule=[window],
        db_path=db,
        tick_interval=5.0,
        window_handlers={"research_decay": research_handler},
    )

    result = sched._dispatch_window(window)

    default_dispatch.assert_not_called()
    research_handler.assert_called_once_with(window_name="research_decay", db_path=db, dry_run=False)
    assert result.success is True
    assert result.signals_total == 4
    assert result.intents_created == 2
    assert result.intents_rejected == 1
    assert result.errors_total == 0


def test_set_window_handler_registers_after_init(db):
    default_dispatch = make_dispatch_fn()
    research_handler = MagicMock(return_value={"items_processed": 3, "actions_taken": 1, "error_count": 1})
    window = ScheduleWindow(name="research_kill_check", hour=14, minute=30, weekdays=frozenset())
    sched = DailyWorkflowScheduler(
        dispatch_fn=default_dispatch,
        schedule=[window],
        db_path=db,
        tick_interval=5.0,
    )
    sched.set_window_handler("research_kill_check", research_handler)

    result = sched.trigger_now("research_kill_check")

    default_dispatch.assert_not_called()
    research_handler.assert_called_once_with(window_name="research_kill_check", db_path=db, dry_run=False)
    assert result["ok"] is True
    assert result["result"]["signals_total"] == 3
    assert result["result"]["intents_created"] == 1
    assert result["result"]["errors_total"] == 1
