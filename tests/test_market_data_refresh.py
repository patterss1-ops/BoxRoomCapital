"""Tests for daily market data refresh (Engine A fuel)."""

from __future__ import annotations

import importlib
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _reload_config(**env_overrides):
    """Reload config module with env overrides."""
    with patch.dict("os.environ", env_overrides, clear=False):
        import config
        return importlib.reload(config)


class TestConfigDefaults:
    """Config defaults for market data refresh."""

    def test_refresh_disabled_by_default(self):
        cfg = _reload_config(MARKET_DATA_REFRESH_ENABLED="")
        assert cfg.MARKET_DATA_REFRESH_ENABLED is False

    def test_refresh_hour_default(self):
        cfg = _reload_config()
        assert cfg.MARKET_DATA_REFRESH_HOUR == 20

    def test_refresh_minute_default(self):
        cfg = _reload_config()
        assert cfg.MARKET_DATA_REFRESH_MINUTE == 0


class TestMarketDataRefreshHandler:
    """Handler calls ingest with correct date range and handles errors."""

    def test_handler_calls_ingest_with_correct_dates(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        mock_ingest = MagicMock(return_value={"bars_ingested": 42, "canonical_rebuilt": 10})
        mock_readiness = MagicMock(return_value={"ready_count": 5, "rows": []})

        with (
            patch("app.engine.control.config") as mock_config,
            patch("research.market_data.bootstrap.ingest_seeded_market_data", mock_ingest),
            patch("research.market_data.bootstrap.market_data_readiness", mock_readiness),
            patch("research.market_data.ingestion.IBKRAdapter"),
        ):
            mock_config.LOG_FILE = "test.log"
            mock_config.MARKET_DATA_REFRESH_ENABLED = True
            mock_config.MARKET_DATA_REFRESH_HOUR = 20
            mock_config.MARKET_DATA_REFRESH_MINUTE = 0
            result = svc._run_market_data_refresh_window("market_data_refresh", "test.db", False)

        assert result["error_count"] == 0
        assert result["items_processed"] == 42
        assert mock_ingest.called

        # Verify date range: yesterday to today
        call_kwargs = mock_ingest.call_args[1]
        assert call_kwargs["start"] == date.today() - timedelta(days=1)
        assert call_kwargs["end"] == date.today()

    def test_handler_catches_errors_without_raising(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        with patch("research.market_data.bootstrap.ingest_seeded_market_data", side_effect=RuntimeError("boom")):
            result = svc._run_market_data_refresh_window("market_data_refresh", "test.db", False)

        assert result["error_count"] == 1
        assert "boom" in result.get("error", "")

    def test_status_persisted_after_run(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        mock_ingest = MagicMock(return_value={"bars_ingested": 10})
        mock_readiness = MagicMock(return_value={"ready_count": 3})

        with (
            patch("research.market_data.bootstrap.ingest_seeded_market_data", mock_ingest),
            patch("research.market_data.bootstrap.market_data_readiness", mock_readiness),
            patch("research.market_data.ingestion.IBKRAdapter"),
        ):
            svc._run_market_data_refresh_window("market_data_refresh", "test.db", False)

        status = svc.market_data_refresh_status()
        assert status["last_result"] is not None
        assert status["last_result"]["status"] == "ok"


class TestSchedulerRegistration:
    """Scheduler registers/skips market data refresh window based on config."""

    def test_scheduler_registers_window_when_enabled(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        with patch("app.engine.control.config") as mock_config:
            mock_config.MARKET_DATA_REFRESH_ENABLED = True
            mock_config.MARKET_DATA_REFRESH_HOUR = 20
            mock_config.MARKET_DATA_REFRESH_MINUTE = 0
            mock_config.ENGINE_A_ENABLED = False
            mock_config.ORCHESTRATOR_DRY_RUN = True
            mock_config.AI_PANEL_ENABLED = False
            mock_config.LOG_FILE = "test.log"

            # Patch scheduler to capture the schedule
            with patch("app.engine.scheduler.DailyWorkflowScheduler") as MockScheduler:
                mock_instance = MagicMock()
                MockScheduler.return_value = mock_instance
                with patch("app.engine.pipeline.dispatch_orchestration"):
                    svc.start_scheduler()

            call_kwargs = MockScheduler.call_args[1]
            window_names = [w.name for w in call_kwargs["schedule"]]
            assert "market_data_refresh" in window_names

    def test_scheduler_skips_window_when_disabled(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        with patch("app.engine.control.config") as mock_config:
            mock_config.MARKET_DATA_REFRESH_ENABLED = False
            mock_config.ENGINE_A_ENABLED = False
            mock_config.ORCHESTRATOR_DRY_RUN = True
            mock_config.AI_PANEL_ENABLED = False
            mock_config.LOG_FILE = "test.log"

            with patch("app.engine.scheduler.DailyWorkflowScheduler") as MockScheduler:
                mock_instance = MagicMock()
                MockScheduler.return_value = mock_instance
                with patch("app.engine.pipeline.dispatch_orchestration"):
                    svc.start_scheduler()

            call_kwargs = MockScheduler.call_args[1]
            window_names = [w.name for w in call_kwargs["schedule"]]
            assert "market_data_refresh" not in window_names


class TestEngineAIndependence:
    """Engine A still runs if refresh fails (independence)."""

    def test_engine_a_runs_after_refresh_failure(self, tmp_path):
        from app.engine.control import BotControlService

        svc = BotControlService(project_root=tmp_path)

        # Simulate refresh failure
        with patch("research.market_data.bootstrap.ingest_seeded_market_data", side_effect=RuntimeError("fail")):
            refresh_result = svc._run_market_data_refresh_window("market_data_refresh", "test.db", False)

        assert refresh_result["error_count"] == 1

        # Engine A should still be callable (its factory is separate)
        mock_factory = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.run_daily.return_value = MagicMock(artifacts=[])
        mock_factory.return_value = mock_pipeline

        svc._engine_a_factory = mock_factory
        result = svc.run_engine_a_validation()
        assert result["status"] == "ok"
