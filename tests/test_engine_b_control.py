import threading
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


def test_engine_b_control_start_submit_stop_and_status(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_B_ENABLED", True)
    monkeypatch.setattr(
        "app.engine.control.research_db_status",
        lambda: {"status": "ready", "schema_ready": True, "reachable": True, "driver_available": True, "configured": True, "detail": "ok"},
    )
    processed = threading.Event()
    seen = {}

    class FakePipeline:
        def process_event(self, raw_content, source_class, source_credibility, source_ids):
            seen["payload"] = {
                "raw_content": raw_content,
                "source_class": source_class,
                "source_credibility": source_credibility,
                "source_ids": source_ids,
            }
            processed.set()
            return SimpleNamespace(
                artifacts=[
                    SimpleNamespace(artifact_type=SimpleNamespace(value="event_card")),
                    SimpleNamespace(artifact_type=SimpleNamespace(value="scoring_result")),
                ],
                outcome=SimpleNamespace(value="experiment"),
                score=83.5,
                blocking_reasons=["needs review"],
            )

    control = BotControlService(tmp_path, engine_b_factory=lambda: FakePipeline())

    started = control.start_engine_b()
    assert started["status"] == "started"

    queued = control.submit_engine_b_event(
        job_id="job-1",
        raw_content="AAPL beat estimates",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["news:1"],
    )
    assert queued["status"] == "queued"
    assert processed.wait(timeout=1.0) is True

    status = control.engine_b_status()
    assert status["running"] is True
    assert status["configured"] is True
    assert status["last_result"]["status"] == "ok"
    assert status["last_result"]["job_id"] == "job-1"
    assert status["last_result"]["artifact_count"] == 2
    assert status["last_result"]["outcome"] == "experiment"
    assert control.pipeline_status()["engine_b"]["running"] is True
    assert control.pipeline_status()["research_db"]["status"] == "ready"
    assert seen["payload"]["source_ids"] == ["news:1"]

    stopped = control.stop_engine_b()
    assert stopped["status"] == "stopped"
    assert control.engine_b_status()["running"] is False


def test_engine_b_submit_runs_ad_hoc_when_service_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_B_ENABLED", False)
    monkeypatch.setattr(
        "app.engine.control.research_db_status",
        lambda: {"status": "schema_missing", "schema_ready": False, "reachable": True, "driver_available": True, "configured": True, "detail": "run init_research_schema()"},
    )
    processed = threading.Event()

    class FakePipeline:
        def process_event(self, raw_content, source_class, source_credibility, source_ids):
            processed.set()
            return SimpleNamespace(
                artifacts=[SimpleNamespace(artifact_type=SimpleNamespace(value="event_card"))],
                outcome=SimpleNamespace(value="park"),
                score=68.0,
                blocking_reasons=[],
            )

    control = BotControlService(tmp_path, engine_b_factory=lambda: FakePipeline())

    queued = control.submit_engine_b_event(
        job_id="job-2",
        raw_content="manual note",
        source_class="manual",
        source_credibility=0.6,
        source_ids=["manual:1"],
        allow_ad_hoc=True,
    )

    assert queued["status"] == "queued"
    assert processed.wait(timeout=1.0) is True
    status = control.engine_b_status()
    assert status["running"] is False
    assert status["enabled"] is False
    assert status["last_result"]["job_id"] == "job-2"
    assert status["last_result"]["status"] == "ok"


def test_engine_b_supervisor_restarts_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.control.OptionsEngine", FakeOptionsEngine)
    monkeypatch.setattr("app.engine.control.config.ENGINE_B_ENABLED", True)
    monkeypatch.setattr(
        "app.engine.control.research_db_status",
        lambda: {"status": "ready", "schema_ready": True, "reachable": True, "driver_available": True, "configured": True, "detail": "ok"},
    )

    class FakePipeline:
        def process_event(self, raw_content, source_class, source_credibility, source_ids):
            return SimpleNamespace(
                artifacts=[],
                outcome=SimpleNamespace(value="reject"),
                score=0.0,
                blocking_reasons=["n/a"],
            )

    control = BotControlService(tmp_path, engine_b_factory=lambda: FakePipeline())
    restarted = control.check_and_restart()

    assert restarted["engine_b"] == "started"
    control.stop_engine_b()
