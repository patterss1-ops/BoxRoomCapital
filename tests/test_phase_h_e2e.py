"""Phase H acceptance harness + release checks.

H-007: End-to-end tests covering all Phase H deliverables.
Validates module imports, promotion enforcement, circuit breaker,
deployment artifacts, and cross-ticket integration.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Module import smoke tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseHModuleImports:
    """Verify all Phase H modules are importable."""

    def test_import_promotion_gate_enforcement(self):
        from fund.promotion_gate import (
            PromotionGateConfig,
            PromotionGateDecision,
            evaluate_promotion_gate,
        )
        assert PromotionGateConfig is not None
        assert PromotionGateDecision is not None
        assert callable(evaluate_promotion_gate)

    def test_import_circuit_breaker(self):
        from broker.circuit_breaker import (
            BrokerCircuitBreaker,
            CircuitBreakerConfig,
            CircuitState,
        )
        assert BrokerCircuitBreaker is not None
        assert CircuitBreakerConfig is not None
        assert CircuitState is not None

    def test_import_orchestrator_promotion_gate_param(self):
        """Verify orchestrator accepts promotion_gate_config parameter."""
        import inspect
        from app.engine.orchestrator import run_orchestration_cycle
        sig = inspect.signature(run_orchestration_cycle)
        assert "promotion_gate_config" in sig.parameters

    def test_import_rebalance_module(self):
        """Verify H-002 rebalance module is importable."""
        from portfolio.rebalance import DriftPlanner
        assert DriftPlanner is not None

    def test_import_metrics_module(self):
        """Verify H-003 metrics module is importable."""
        from app.metrics import (
            build_api_health_payload,
            build_metrics_payload,
            build_prometheus_metrics_payload,
            render_prometheus_metrics,
        )
        assert callable(build_api_health_payload)
        assert callable(build_metrics_payload)
        assert callable(build_prometheus_metrics_payload)
        assert callable(render_prometheus_metrics)

    def test_import_eod_reconciliation_module(self):
        """Verify H-005 EOD reconciliation module is importable."""
        from fund.eod_reconciliation import (
            EODReconciliationReport,
            dispatch_eod_reconciliation,
            run_eod_reconciliation,
        )
        assert EODReconciliationReport is not None
        assert callable(dispatch_eod_reconciliation)
        assert callable(run_eod_reconciliation)

    def test_import_existing_promotion_gate(self):
        """Verify existing C-004 promotion gate still works."""
        from fund.promotion_gate import (
            build_promotion_gate_report,
            validate_lane_transition,
        )
        assert callable(build_promotion_gate_report)
        assert callable(validate_lane_transition)


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Promotion enforcement E2E (H-001)
# ═══════════════════════════════════════════════════════════════════════════


class TestPromotionEnforcementE2E:
    """End-to-end promotion enforcement through the orchestration pipeline."""

    def _init_db(self, tmp_path):
        from data import trade_db
        db_path = tmp_path / "h007_e2e.db"
        trade_db.init_db(str(db_path))
        return str(db_path)

    def test_full_promotion_pipeline_blocks_then_allows(self, tmp_path):
        """Strategy must complete shadow→staged→live before entries are allowed."""
        from data import trade_db
        from fund.promotion_gate import (
            PromotionGateConfig,
            evaluate_promotion_gate,
        )

        db_path = self._init_db(tmp_path)
        strategy = "test_strategy"
        config = PromotionGateConfig(enabled=True, min_soak_hours=0, max_stale_hours=0)

        # No live set → blocked
        d1 = evaluate_promotion_gate(strategy, is_exit=False, config=config, db_path=db_path)
        assert not d1.allowed
        assert d1.reason_code == "NO_LIVE_SET"

        # Create and promote through pipeline
        s = trade_db.create_strategy_parameter_set(
            strategy_key=strategy,
            name="test-set",
            parameters_payload=json.dumps({"k": "v"}),
            status="shadow",
            db_path=db_path,
        )
        trade_db.promote_strategy_parameter_set(
            set_id=s["id"], to_status="staged_live",
            actor="test", acknowledgement="test promote", db_path=db_path,
        )
        trade_db.promote_strategy_parameter_set(
            set_id=s["id"], to_status="live",
            actor="test", acknowledgement="test promote", db_path=db_path,
        )

        # Now has live set → allowed
        d2 = evaluate_promotion_gate(strategy, is_exit=False, config=config, db_path=db_path)
        assert d2.allowed
        assert d2.reason_code == "PROMOTION_GATE_PASSED"

    def test_exits_always_bypass_promotion_gate(self, tmp_path):
        """Exit orders must never be blocked by promotion enforcement."""
        from fund.promotion_gate import PromotionGateConfig, evaluate_promotion_gate

        db_path = self._init_db(tmp_path)
        config = PromotionGateConfig(enabled=True, bypass_for_exits=True)

        decision = evaluate_promotion_gate(
            "nonexistent_strategy", is_exit=True, config=config, db_path=db_path,
        )
        assert decision.allowed
        assert decision.reason_code == "EXIT_BYPASS"


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Circuit breaker E2E (H-006)
# ═══════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerE2E:
    """End-to-end circuit breaker state machine validation."""

    def test_full_lifecycle_closed_to_open_to_recovery(self):
        """Circuit trips after failures, recovers after timeout, probe succeeds."""
        from broker.circuit_breaker import (
            BrokerCircuitBreaker,
            CircuitBreakerConfig,
            CircuitState,
        )

        cb = BrokerCircuitBreaker(
            "e2e_broker",
            config=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout_secs=0.01,
                half_open_max_calls=1,
            ),
        )

        # Phase 1: Normal operation
        assert cb.state == CircuitState.CLOSED
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # Phase 2: Failures trip the circuit
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Phase 3: Recovery after timeout
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

        # Phase 4: Probe succeeds → closed
        decision = cb.check()
        assert decision.allowed
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # Verify stats
        stats = cb.get_stats()
        assert stats.total_successes == 2
        assert stats.total_failures == 3
        assert stats.trips_total == 1

    def test_multiple_brokers_isolated(self):
        """Different brokers have independent circuit breakers."""
        from broker.circuit_breaker import (
            BrokerCircuitBreaker,
            CircuitBreakerConfig,
            CircuitState,
        )

        cb_a = BrokerCircuitBreaker("broker_a", CircuitBreakerConfig(failure_threshold=1))
        cb_b = BrokerCircuitBreaker("broker_b", CircuitBreakerConfig(failure_threshold=1))

        cb_a.record_failure()
        assert cb_a.state == CircuitState.OPEN
        assert cb_b.state == CircuitState.CLOSED


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Deployment artifacts (H-004)
# ═══════════════════════════════════════════════════════════════════════════


class TestDeploymentArtifactsE2E:
    """Validate deployment packaging artifacts exist and are consistent."""

    def test_dockerfile_references_entrypoint(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        content = open(path).read()
        assert "run_console.py" in content

    def test_compose_and_dockerfile_consistent_port(self):
        dockerfile = open(os.path.join(PROJECT_ROOT, "Dockerfile")).read()
        compose = open(os.path.join(PROJECT_ROOT, "docker-compose.yml")).read()
        assert "8000" in dockerfile
        assert "8000" in compose

    def test_env_example_covers_all_required_keys(self):
        content = open(os.path.join(PROJECT_ROOT, ".env.example")).read()
        required_keys = [
            "BROKER_MODE", "IG_API_KEY", "LOG_LEVEL",
            "XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "GOOGLE_AI_API_KEY", "CONTROL_PLANE_PORT",
        ]
        for key in required_keys:
            assert f"{key}=" in content, f"Missing required key: {key}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 4b: Prometheus metrics E2E (H-003)
# ═══════════════════════════════════════════════════════════════════════════


class TestPrometheusMetricsE2E:
    """End-to-end validation of Prometheus metrics pipeline."""

    def test_health_payload_structure(self, tmp_path):
        """Health endpoint returns structured dependency checks."""
        from data import trade_db
        from app.metrics import build_api_health_payload

        db_path = str(tmp_path / "metrics_e2e.db")
        trade_db.init_db(db_path)
        payload = build_api_health_payload(db_path=db_path)

        assert payload["status"] in {"ok", "degraded"}
        assert "generated_at" in payload
        assert "db" in payload["checks"]
        assert payload["checks"]["db"]["status"] == "ok"

    def test_prometheus_text_format_valid(self, tmp_path):
        """Prometheus text output follows exposition format conventions."""
        from data import trade_db
        from app.metrics import build_prometheus_metrics_payload

        db_path = str(tmp_path / "prom_e2e.db")
        trade_db.init_db(db_path)
        text = build_prometheus_metrics_payload(days=7, db_path=db_path)

        # Must contain HELP and TYPE lines
        assert "# HELP" in text
        assert "# TYPE" in text
        # Must contain expected metric names
        assert "brc_signal_scoring_total_24h" in text
        assert "brc_execution_fill_rate_pct" in text
        assert "brc_execution_mean_latency_ms" in text
        # Must end with newline
        assert text.endswith("\n")

    def test_metrics_server_endpoints_wired(self):
        """Server has /api/health and /api/metrics endpoints registered."""
        from app.api.server import create_app
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/api/health" in routes
        assert "/api/metrics" in routes

    def test_rebalance_dispatch_wired_in_pipeline(self):
        """Verify H-002 rebalance dispatch function exists in pipeline."""
        from app.engine.pipeline import dispatch_rebalance_check
        assert callable(dispatch_rebalance_check)


# ═══════════════════════════════════════════════════════════════════════════
# Section 4c: EOD Reconciliation E2E (H-005)
# ═══════════════════════════════════════════════════════════════════════════


class TestEODReconciliationE2E:
    """End-to-end validation of EOD reconciliation pipeline."""

    def _init_db(self, tmp_path):
        from data import trade_db
        db_path = str(tmp_path / "eod_e2e.db")
        trade_db.init_db(db_path)
        return db_path

    def test_eod_reconciliation_full_flow(self, tmp_path):
        """Full EOD reconciliation produces structured report."""
        from fund.eod_reconciliation import run_eod_reconciliation

        db_path = self._init_db(tmp_path)
        report = run_eod_reconciliation(report_date="2026-03-03", db_path=db_path)
        assert report.status == "clean"
        d = report.to_dict()
        assert "pnl_by_strategy" in d
        assert "pnl_by_sleeve" in d
        assert "mismatches" in d

    def test_eod_dispatch_callable_by_scheduler(self, tmp_path):
        """Dispatch function returns scheduler-compatible dict."""
        from fund.eod_reconciliation import dispatch_eod_reconciliation

        db_path = self._init_db(tmp_path)
        result = dispatch_eod_reconciliation(
            window_name="us_close_eod",
            db_path=db_path,
            report_date="2026-03-03",
        )
        assert result["window_name"] == "us_close_eod"
        assert result["status"] == "clean"


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Cross-ticket integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossTicketIntegration:
    """Validate that Phase H deliverables integrate correctly."""

    def test_promotion_gate_and_orchestrator_wired(self):
        """Verify orchestrator can invoke promotion gate."""
        import inspect
        from app.engine.orchestrator import run_orchestration_cycle
        sig = inspect.signature(run_orchestration_cycle)
        param = sig.parameters.get("promotion_gate_config")
        assert param is not None
        assert param.default is None  # Optional, disabled by default

    def test_circuit_breaker_compatible_with_dispatcher_pattern(self):
        """Circuit breaker check/record pattern matches dispatcher flow."""
        from broker.circuit_breaker import BrokerCircuitBreaker, CircuitState

        cb = BrokerCircuitBreaker("paper")

        # Simulate dispatcher flow
        decision = cb.check()
        assert decision.allowed

        # Simulate successful broker call
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_promotion_gate_backward_compatible(self):
        """Existing build_promotion_gate_report still works after H-001 changes."""
        from fund.promotion_gate import build_promotion_gate_report
        from data import trade_db

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "compat.db")
            trade_db.init_db(db_path)
            report = build_promotion_gate_report(db_path=db_path)
            assert "recommendation" in report
            assert "lanes" in report
            assert "action" in report["recommendation"]


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Phase H source file presence
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseHSourceFiles:
    """Validate all Phase H source files exist."""

    REQUIRED_FILES = [
        "fund/promotion_gate.py",
        "broker/circuit_breaker.py",
        "Dockerfile",
        "docker-compose.yml",
        ".env.example",
        "app/engine/orchestrator.py",
        "execution/dispatcher.py",
        "app/metrics.py",
        "portfolio/rebalance.py",
        "fund/eod_reconciliation.py",
    ]

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_source_file_exists(self, rel_path):
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full_path), f"Missing: {rel_path}"
