import threading
import time
from types import SimpleNamespace

from app.engine.control import BotControlService


class FakeOptionsEngine:
    def status(self):
        return {"running": False, "mode": "paper"}

    def start(self, mode):
        return {"status": "started", "mode": mode}

    def stop(self):
        return {"status": "stopped"}

    def pause(self):
        return {"status": "paused"}

    def resume(self):
        return {"status": "running"}

    def scan_now(self, mode):
        return {"status": "ok", "mode": mode}

    def reconcile(self):
        return {"status": "ok"}

    def reconcile_report(self):
        return {"status": "ok"}

    def close_spread(self, **kwargs):
        return {"status": "ok"}

    def set_kill_switch(self, **kwargs):
        return {"status": "ok"}

    def set_risk_throttle(self, **kwargs):
        return {"status": "ok"}

    def set_market_cooldown(self, **kwargs):
        return {"status": "ok"}

    def clear_market_cooldown(self, **kwargs):
        return {"status": "ok"}


def test_engine_a_control_start_stop_and_status(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_A_ENABLED", True)
    monkeypatch.setattr("app.engine.control.config.ENGINE_A_INTERVAL_SECONDS", 0.05)
    ran = threading.Event()
    calls = {"count": 0}

    class FakePipeline:
        def run_daily(self, as_of: str):
            calls["count"] += 1
            ran.set()
            return type("Result", (), {"artifacts": [1, 2, 3]})()

    control = BotControlService(tmp_path, engine_a_factory=lambda: FakePipeline())

    started = control.start_engine_a()
    assert started["status"] == "started"
    assert ran.wait(timeout=1.0) is True

    status = control.engine_a_status()
    assert status["running"] is True
    assert status["configured"] is True
    assert status["last_result"]["status"] == "ok"
    assert calls["count"] >= 1
    assert control.pipeline_status()["engine_a"]["running"] is True

    stopped = control.stop_engine_a()
    assert stopped["status"] == "stopped"
    assert control.engine_a_status()["running"] is False


def test_engine_a_control_disabled_without_config(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_A_ENABLED", False)

    control = BotControlService(tmp_path)

    assert control.start_engine_a()["status"] == "disabled"


def test_engine_a_supervisor_restarts_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_A_ENABLED", True)
    monkeypatch.setattr("app.engine.control.config.ENGINE_A_INTERVAL_SECONDS", 60)

    class FakePipeline:
        def run_daily(self, as_of: str):
            return type("Result", (), {"artifacts": []})()

    control = BotControlService(tmp_path, engine_a_factory=lambda: FakePipeline())
    restarted = control.check_and_restart()

    assert restarted["engine_a"] == "started"
    control.stop_engine_a()


def test_decay_review_window_updates_status(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)

    class FakeDecayReviewService:
        def run_decay_check(self, as_of: str, db_path: str | None = None):
            assert db_path == "scheduler.db"
            return [object(), object()]

    control = BotControlService(tmp_path, decay_review_factory=lambda: FakeDecayReviewService())

    result = control._run_decay_review_window("research_decay_review_06", "scheduler.db", dry_run=False)

    assert result == {"artifacts_created": 2, "pending_reviews": 2, "error_count": 0}
    status = control.pipeline_status()["decay_review"]
    assert status["configured"] is True
    assert status["last_result"]["pending_reviews"] == 2


def test_kill_check_window_executes_auto_kills(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    executed = []

    class FakeKillMonitor:
        def check_all(self, as_of: str):
            return [
                SimpleNamespace(hypothesis_id="hyp-1", trigger="drawdown", trigger_detail="dd", auto_kill=True),
                SimpleNamespace(hypothesis_id="hyp-2", trigger="decay", trigger_detail="decay", auto_kill=False),
            ]

        def execute_kill(self, **kwargs):
            executed.append(kwargs)

    control = BotControlService(tmp_path, kill_monitor_factory=lambda: FakeKillMonitor())

    result = control._run_kill_check_window("research_kill_check_14", "scheduler.db", dry_run=False)

    assert result == {"items_processed": 2, "actions_taken": 1, "skipped": 1, "error_count": 0}
    assert executed == [
        {
            "hypothesis_id": "hyp-1",
            "trigger": "drawdown",
            "trigger_detail": "dd",
            "operator_approved": False,
        }
    ]
    status = control.pipeline_status()["kill_check"]
    assert status["configured"] is True
    assert status["last_result"]["auto_kills"] == 1


def test_scheduler_registers_research_windows_when_factories_present(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_A_ENABLED", True)
    monkeypatch.setattr("app.engine.pipeline.dispatch_orchestration", lambda **kwargs: {"signals_total": 0})
    monkeypatch.setattr("app.engine.scheduler.DEFAULT_SCHEDULE", [])

    class FakeScheduler:
        def __init__(self, dispatch_fn, schedule=None, window_handlers=None, **kwargs):
            self.dispatch_fn = dispatch_fn
            self.schedule = list(schedule or [])
            self.window_handlers = dict(window_handlers or {})

        def start(self):
            return {"ok": True}

        def stop(self):
            return {"ok": True}

        def status(self):
            return {"running": True, "schedule": [{"name": w.name} for w in self.schedule]}

    monkeypatch.setattr("app.engine.scheduler.DailyWorkflowScheduler", FakeScheduler)

    control = BotControlService(
        tmp_path,
        engine_a_factory=lambda: object(),
        decay_review_factory=lambda: object(),
        kill_monitor_factory=lambda: object(),
    )

    started = control.start_scheduler()

    assert started["status"] == "started"
    names = [window.name for window in control._scheduler.schedule]
    assert "engine_a_close_research" in names
    assert "research_decay_review_00" in names
    assert "research_decay_review_18" in names
    assert "research_kill_check_14" in names
    assert "research_kill_check_20" in names
    assert "engine_a_close_research" in control._scheduler.window_handlers
    assert "research_decay_review_12" in control._scheduler.window_handlers
    assert "research_kill_check_16" in control._scheduler.window_handlers
