"""Tests for K-001 system health dashboard."""

from __future__ import annotations

from app.health_dashboard import (
    HealthDashboard,
    HealthStatus,
    SubsystemHealth,
    SystemHealthReport,
)


class TestSubsystemHealth:
    def test_to_dict(self):
        h = SubsystemHealth(name="broker", status=HealthStatus.HEALTHY, message="ok")
        d = h.to_dict()
        assert d["name"] == "broker"
        assert d["status"] == "healthy"

    def test_defaults(self):
        h = SubsystemHealth(name="test")
        assert h.status == HealthStatus.UNKNOWN
        assert h.message == ""


class TestHealthDashboard:
    def test_empty_dashboard(self):
        dash = HealthDashboard()
        report = dash.get_report()
        assert report.overall_status == HealthStatus.UNKNOWN
        assert len(report.subsystems) == 0

    def test_manual_update(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY, "connected")
        dash.update_status("data", HealthStatus.HEALTHY, "fresh")

        report = dash.get_report()
        assert report.overall_status == HealthStatus.HEALTHY
        assert report.healthy_count == 2

    def test_degraded_overrides_healthy(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY)
        dash.update_status("data", HealthStatus.DEGRADED, "stale feeds")

        report = dash.get_report()
        assert report.overall_status == HealthStatus.DEGRADED
        assert report.degraded_count == 1
        assert report.healthy_count == 1

    def test_critical_overrides_degraded(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.DEGRADED)
        dash.update_status("data", HealthStatus.CRITICAL, "provider down")

        report = dash.get_report()
        assert report.overall_status == HealthStatus.CRITICAL
        assert report.critical_count == 1

    def test_register_and_run_check(self):
        dash = HealthDashboard()

        def broker_check():
            return SubsystemHealth(name="broker", status=HealthStatus.HEALTHY, message="connected")

        dash.register_check("broker", broker_check)
        report = dash.run_checks()
        assert report.overall_status == HealthStatus.HEALTHY
        assert report.healthy_count == 1

    def test_check_failure_marks_critical(self):
        dash = HealthDashboard()

        def failing_check():
            raise RuntimeError("connection refused")

        dash.register_check("broker", failing_check)
        report = dash.run_checks()
        assert report.overall_status == HealthStatus.CRITICAL
        assert "connection refused" in dash.get_subsystem("broker").message

    def test_check_returning_non_health(self):
        """Check function returning non-SubsystemHealth treated as healthy."""
        dash = HealthDashboard()
        dash.register_check("simple", lambda: True)
        report = dash.run_checks()
        assert report.healthy_count == 1

    def test_get_subsystem(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY)
        sub = dash.get_subsystem("broker")
        assert sub is not None
        assert sub.name == "broker"

    def test_get_subsystem_missing(self):
        dash = HealthDashboard()
        assert dash.get_subsystem("nonexistent") is None

    def test_mixed_checks_and_manual(self):
        dash = HealthDashboard()
        dash.update_status("manual", HealthStatus.HEALTHY)
        dash.register_check("auto", lambda: SubsystemHealth(name="auto", status=HealthStatus.HEALTHY))

        report = dash.run_checks()
        assert len(report.subsystems) == 2
        assert report.healthy_count == 2

    def test_report_to_dict(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY)
        report = dash.get_report()
        d = report.to_dict()
        assert d["overall_status"] == "healthy"
        assert "subsystems" in d
        assert len(d["subsystems"]) == 1

    def test_update_overwrites_previous(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY)
        dash.update_status("broker", HealthStatus.CRITICAL, "timeout")
        assert dash.get_subsystem("broker").status == HealthStatus.CRITICAL

    def test_details_kwarg(self):
        dash = HealthDashboard()
        dash.update_status("broker", HealthStatus.HEALTHY, latency_ms=42, connections=3)
        sub = dash.get_subsystem("broker")
        assert sub.details["latency_ms"] == 42
        assert sub.details["connections"] == 3
