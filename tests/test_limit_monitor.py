"""Tests for K-003 risk limit monitoring daemon."""

from __future__ import annotations

from risk.limit_monitor import (
    LimitCheckResult,
    LimitConfig,
    LimitMonitor,
    LimitMonitorReport,
    LimitStatus,
)


class TestLimitConfig:
    def test_basic_config(self):
        lc = LimitConfig(name="max_position", warn_threshold=4.0, hard_limit=5.0)
        assert lc.name == "max_position"
        assert lc.warn_threshold == 4.0
        assert lc.hard_limit == 5.0


class TestLimitCheckResult:
    def test_to_dict(self):
        r = LimitCheckResult(
            name="max_dd", current_value=3.5, warn_threshold=4.0,
            hard_limit=5.0, status=LimitStatus.OK, utilisation_pct=70.0,
            headroom=1.5,
        )
        d = r.to_dict()
        assert d["status"] == "ok"
        assert d["utilisation_pct"] == 70.0


class TestLimitMonitor:
    def test_add_and_check_ok(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))
        result = mon.check_limit("heat", current_value=30.0)
        assert result.status == LimitStatus.OK
        assert result.headroom == 20.0

    def test_warning_threshold(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))
        result = mon.check_limit("heat", current_value=42.0)
        assert result.status == LimitStatus.WARNING

    def test_breach_threshold(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))
        result = mon.check_limit("heat", current_value=55.0)
        assert result.status == LimitStatus.BREACH
        assert result.utilisation_pct > 100.0

    def test_metric_fn(self):
        mon = LimitMonitor()
        counter = {"value": 25.0}
        mon.add_limit(LimitConfig(
            "position", warn_threshold=80, hard_limit=100,
            metric_fn=lambda: counter["value"],
        ))
        result = mon.check_limit("position")
        assert result.current_value == 25.0
        assert result.status == LimitStatus.OK

    def test_check_unknown_raises(self):
        mon = LimitMonitor()
        import pytest
        with pytest.raises(KeyError, match="Unknown limit"):
            mon.check_limit("nonexistent")

    def test_remove_limit(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))
        assert mon.remove_limit("heat") is True
        assert mon.remove_limit("heat") is False

    def test_check_all(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))
        mon.add_limit(LimitConfig("conc", warn_threshold=15, hard_limit=20))

        report = mon.check_all()
        assert len(report.results) == 2
        assert report.overall_status == LimitStatus.OK

    def test_check_all_with_breach(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50, metric_fn=lambda: 30))
        mon.add_limit(LimitConfig("conc", warn_threshold=15, hard_limit=20, metric_fn=lambda: 25))

        report = mon.check_all()
        assert report.overall_status == LimitStatus.BREACH
        assert report.breaches == 1
        assert report.warnings == 0

    def test_check_all_with_warning(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50, metric_fn=lambda: 45))
        mon.add_limit(LimitConfig("conc", warn_threshold=15, hard_limit=20, metric_fn=lambda: 10))

        report = mon.check_all()
        assert report.overall_status == LimitStatus.WARNING
        assert report.warnings == 1

    def test_alert_fn_called(self):
        alerts = []
        mon = LimitMonitor(alert_fn=lambda level, r: alerts.append((level, r.name)))
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))

        mon.check_limit("heat", current_value=42)
        assert len(alerts) == 1
        assert alerts[0] == ("warning", "heat")

    def test_alert_fn_not_called_for_ok(self):
        alerts = []
        mon = LimitMonitor(alert_fn=lambda level, r: alerts.append(r.name))
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))

        mon.check_limit("heat", current_value=30)
        assert len(alerts) == 0

    def test_history_tracking(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))
        mon.check_limit("heat", current_value=30)
        mon.check_limit("heat", current_value=45)
        assert len(mon.history) == 2

    def test_report_to_dict(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50, metric_fn=lambda: 30))
        report = mon.check_all()
        d = report.to_dict()
        assert "overall_status" in d
        assert "results" in d
        assert len(d["results"]) == 1

    def test_zero_hard_limit(self):
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("zero", warn_threshold=0, hard_limit=0))
        result = mon.check_limit("zero", current_value=5)
        assert result.status == LimitStatus.OK  # Can't divide by zero
