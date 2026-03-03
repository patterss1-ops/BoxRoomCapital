"""Phase K acceptance harness + release checks.

K-007: End-to-end tests covering all Phase K deliverables.
Validates health dashboard, limit monitor, runbook generator,
and cross-module integration.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Module import smoke tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseKModuleImports:
    """Verify all Phase K modules are importable."""

    def test_import_health_dashboard(self):
        from app.health_dashboard import HealthDashboard, HealthStatus, SubsystemHealth
        assert HealthDashboard is not None
        assert HealthStatus is not None
        assert SubsystemHealth is not None

    def test_import_limit_monitor(self):
        from risk.limit_monitor import LimitConfig, LimitMonitor, LimitStatus
        assert LimitMonitor is not None
        assert LimitConfig is not None
        assert LimitStatus is not None

    def test_import_runbook_generator(self):
        from ops.runbook_generator import ChecklistItem, Runbook, RunbookGenerator
        assert RunbookGenerator is not None
        assert Runbook is not None
        assert ChecklistItem is not None


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Health dashboard E2E (K-001)
# ═══════════════════════════════════════════════════════════════════════════


class TestHealthDashboardE2E:
    """End-to-end health dashboard validation."""

    def test_full_system_health_check(self):
        """Register multiple checks, run them, verify aggregation."""
        from app.health_dashboard import HealthDashboard, HealthStatus, SubsystemHealth

        dash = HealthDashboard()

        # Simulate subsystem checks
        dash.register_check("broker", lambda: SubsystemHealth(
            name="broker", status=HealthStatus.HEALTHY, message="IG connected"))
        dash.register_check("data", lambda: SubsystemHealth(
            name="data", status=HealthStatus.HEALTHY, message="yfinance active"))
        dash.register_check("signal", lambda: SubsystemHealth(
            name="signal", status=HealthStatus.DEGRADED, message="L3 stale"))

        report = dash.run_checks()
        assert report.overall_status == HealthStatus.DEGRADED
        assert report.healthy_count == 2
        assert report.degraded_count == 1

    def test_all_healthy_report(self):
        from app.health_dashboard import HealthDashboard, HealthStatus

        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY)
        dash.update_status("data", HealthStatus.HEALTHY)
        dash.update_status("signal", HealthStatus.HEALTHY)
        dash.update_status("risk", HealthStatus.HEALTHY)

        report = dash.get_report()
        assert report.overall_status == HealthStatus.HEALTHY
        assert report.healthy_count == 4
        d = report.to_dict()
        assert d["overall_status"] == "healthy"


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Limit monitor E2E (K-003)
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitMonitorE2E:
    """End-to-end risk limit monitoring validation."""

    def test_full_limit_check_cycle(self):
        """Register limits, check with varying values, verify alerts."""
        from risk.limit_monitor import LimitConfig, LimitMonitor, LimitStatus

        alerts = []
        mon = LimitMonitor(alert_fn=lambda level, r: alerts.append((level, r.name)))

        # Portfolio heat limit
        mon.add_limit(LimitConfig("portfolio_heat", warn_threshold=40.0, hard_limit=50.0))
        # Strategy concentration
        mon.add_limit(LimitConfig("strategy_conc", warn_threshold=15.0, hard_limit=20.0))
        # Daily drawdown
        mon.add_limit(LimitConfig("daily_dd", warn_threshold=3.0, hard_limit=5.0))

        # All OK
        mon.check_limit("portfolio_heat", current_value=30.0)
        mon.check_limit("strategy_conc", current_value=10.0)
        mon.check_limit("daily_dd", current_value=1.0)
        assert len(alerts) == 0

        # Warning on heat
        mon.check_limit("portfolio_heat", current_value=45.0)
        assert len(alerts) == 1
        assert alerts[-1] == ("warning", "portfolio_heat")

        # Breach on drawdown
        mon.check_limit("daily_dd", current_value=6.0)
        assert len(alerts) == 2
        assert alerts[-1] == ("breach", "daily_dd")

    def test_check_all_report(self):
        from risk.limit_monitor import LimitConfig, LimitMonitor, LimitStatus

        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50, metric_fn=lambda: 42))
        mon.add_limit(LimitConfig("conc", warn_threshold=15, hard_limit=20, metric_fn=lambda: 10))

        report = mon.check_all()
        assert report.overall_status == LimitStatus.WARNING
        d = report.to_dict()
        assert d["warnings"] == 1
        assert d["breaches"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Runbook generator E2E (K-005)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunbookGeneratorE2E:
    """End-to-end runbook generation validation."""

    def test_complete_daily_workflow(self):
        """Generate pre-market + post-market runbooks."""
        from ops.runbook_generator import RunbookGenerator

        gen = RunbookGenerator(
            strategies=["ibs", "gtaa", "momentum"],
            brokers=["ig", "ibkr"],
        )

        pre = gen.generate_pre_market()
        post = gen.generate_post_market()

        assert pre.phase == "pre_market"
        assert post.phase == "post_market"
        assert len(pre.items) > 5
        assert len(post.items) > 3

        # Verify text output works
        pre_text = pre.to_text()
        assert "Pre-Market" in pre_text
        assert "[AUTO]" in pre_text

    def test_incident_runbooks(self):
        """Generate incident-specific runbooks."""
        from ops.runbook_generator import RunbookGenerator

        gen = RunbookGenerator()
        broker_rb = gen.generate_incident("broker_disconnect")
        data_rb = gen.generate_incident("data_stale")

        assert broker_rb.context["incident_type"] == "broker_disconnect"
        assert data_rb.context["incident_type"] == "data_stale"
        assert len(broker_rb.items) > 0
        assert len(data_rb.items) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Cross-module integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossModuleIntegration:
    """Validate Phase K modules integrate correctly."""

    def test_health_dashboard_drives_runbook(self):
        """Health status informs runbook content."""
        from app.health_dashboard import HealthDashboard, HealthStatus
        from ops.runbook_generator import RunbookGenerator

        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.CRITICAL, "disconnected")

        report = dash.get_report()
        if report.overall_status == HealthStatus.CRITICAL:
            gen = RunbookGenerator()
            rb = gen.generate_incident("broker_disconnect")
            assert len(rb.items) > 0

    def test_limit_monitor_feeds_health_dashboard(self):
        """Limit breaches update health dashboard."""
        from app.health_dashboard import HealthDashboard, HealthStatus
        from risk.limit_monitor import LimitConfig, LimitMonitor, LimitStatus

        dash = HealthDashboard()
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))

        result = mon.check_limit("heat", current_value=55)
        if result.status == LimitStatus.BREACH:
            dash.update_status("risk_limits", HealthStatus.CRITICAL, f"BREACH: {result.name}")

        report = dash.get_report()
        assert report.overall_status == HealthStatus.CRITICAL

    def test_backward_compatible_with_phase_i(self):
        """Phase I alert router coexists with Phase K health dashboard."""
        from app.alert_router import AlertRouter
        from app.health_dashboard import HealthDashboard, HealthStatus

        # Both should coexist without conflict
        router = AlertRouter(notify_fn=lambda msg, icon: True)
        dash = HealthDashboard()
        dash.update_status("alert_router", HealthStatus.HEALTHY)
        assert dash.get_report().overall_status == HealthStatus.HEALTHY


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Source file presence
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseKSourceFiles:
    """Validate all Phase K source files exist."""

    REQUIRED_FILES = [
        "app/health_dashboard.py",
        "risk/limit_monitor.py",
        "ops/runbook_generator.py",
    ]

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_source_file_exists(self, rel_path):
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full_path), f"Missing: {rel_path}"
