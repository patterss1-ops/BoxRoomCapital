"""Phase K acceptance harness + release checks.

K-007: End-to-end tests covering all Phase K deliverables.
Validates health dashboard, limit monitor, runbook generator,
trade journal, live attribution, config validator,
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

    def test_import_trade_journal(self):
        from ops.trade_journal import JournalSummary, TradeJournal, TradeJournalEntry
        assert TradeJournal is not None
        assert TradeJournalEntry is not None
        assert JournalSummary is not None

    def test_import_live_attribution(self):
        from analytics.live_attribution import LiveAttributionEngine, LivePnL, PortfolioSnapshot
        assert LiveAttributionEngine is not None
        assert LivePnL is not None
        assert PortfolioSnapshot is not None

    def test_import_config_validator(self):
        from ops.config_validator import ConfigValidator, ValidationReport, ValidationSeverity
        assert ConfigValidator is not None
        assert ValidationReport is not None
        assert ValidationSeverity is not None


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
# Section 5: Trade journal E2E (K-002)
# ═══════════════════════════════════════════════════════════════════════════


class TestTradeJournalE2E:
    """End-to-end trade journal validation."""

    def test_full_trade_lifecycle(self):
        """Record trade lifecycle events and query audit trail."""
        from ops.trade_journal import TradeJournal, TradeJournalEntry

        journal = TradeJournal()

        # Record lifecycle events
        journal.add_entry(TradeJournalEntry(
            trade_id="T001", timestamp="2026-03-03T09:00:00Z",
            strategy="ibs", symbol="AAPL", side="buy",
            quantity=100, price=150.0, broker="ig",
            status="submitted", tags=["opening"],
        ))
        journal.add_entry(TradeJournalEntry(
            trade_id="T001", timestamp="2026-03-03T09:01:00Z",
            strategy="ibs", symbol="AAPL", side="buy",
            quantity=100, price=150.25, broker="ig",
            status="filled", tags=["opening", "filled"],
        ))

        trail = journal.get_audit_trail("T001")
        assert len(trail) == 2
        assert trail[0].status == "submitted"
        assert trail[1].status == "filled"

    def test_multi_strategy_query(self):
        """Query across multiple strategies and symbols."""
        from ops.trade_journal import TradeJournal, TradeJournalEntry

        journal = TradeJournal()
        for i, (strat, sym) in enumerate([
            ("ibs", "AAPL"), ("gtaa", "SPY"), ("ibs", "MSFT"), ("gtaa", "TLT"),
        ]):
            journal.add_entry(TradeJournalEntry(
                trade_id=f"T{i:03d}", timestamp=f"2026-03-03T10:{i:02d}:00Z",
                strategy=strat, symbol=sym, side="buy",
                quantity=50, price=100.0, broker="ig", status="filled",
            ))

        ibs = journal.query(strategy="ibs")
        assert len(ibs) == 2
        summary = journal.get_summary()
        assert summary.total_trades == 4
        assert len(summary.unique_strategies) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Live attribution E2E (K-004)
# ═══════════════════════════════════════════════════════════════════════════


class TestLiveAttributionE2E:
    """End-to-end live attribution validation."""

    def test_multi_strategy_attribution(self):
        """Track PnL across multiple strategies and verify attribution."""
        from analytics.live_attribution import LiveAttributionEngine

        engine = LiveAttributionEngine(
            strategies=["ibs", "gtaa", "momentum"],
            initial_nav=100000.0,
        )

        engine.update_pnl("ibs", unrealised=500, realised=200)
        engine.update_pnl("gtaa", unrealised=-100, realised=300)
        engine.update_pnl("momentum", unrealised=150, realised=50)

        snap = engine.take_snapshot()
        assert snap.total_nav == 101100.0  # 100000 + 700 + 200 + 200
        assert snap.daily_pnl == 1100.0
        assert len(snap.strategy_pnls) == 3

        # Verify contributions sum to 100%
        total_contrib = sum(p.contribution_pct for p in snap.strategy_pnls)
        assert abs(total_contrib - 100.0) < 0.01

    def test_reset_and_new_day(self):
        """Reset daily PnL and start fresh."""
        from analytics.live_attribution import LiveAttributionEngine

        engine = LiveAttributionEngine(strategies=["ibs"])
        engine.update_pnl("ibs", unrealised=500, realised=200)
        snap1 = engine.take_snapshot()
        assert snap1.daily_pnl == 700.0

        engine.reset_daily()
        snap2 = engine.take_snapshot()
        assert snap2.daily_pnl == 0.0
        assert len(engine.history) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Config validator E2E (K-006)
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigValidatorE2E:
    """End-to-end config validation."""

    def test_full_config_validation(self):
        """Validate a realistic trading system config."""
        from ops.config_validator import ConfigValidator, ValidationSeverity

        v = ConfigValidator()
        v.add_required_rule("broker_api_key")
        v.add_required_rule("data_provider")
        v.add_range_rule("max_position_pct", 0, 100)
        v.add_range_rule("max_portfolio_heat", 0, 100)
        v.add_type_rule("strategies", list)
        v.add_cross_ref_rule(
            "warn_threshold", "hard_limit",
            lambda w, h: w < h,
            "warn must be below hard limit",
        )

        config = {
            "broker_api_key": "test-key-123",
            "data_provider": "yfinance",
            "max_position_pct": 10,
            "max_portfolio_heat": 50,
            "strategies": ["ibs", "gtaa"],
            "warn_threshold": 40,
            "hard_limit": 50,
        }

        report = v.validate(config)
        assert report.passed
        assert report.errors == 0

    def test_invalid_config_catches_errors(self):
        """Validate config with multiple errors."""
        from ops.config_validator import ConfigValidator, ValidationSeverity

        v = ConfigValidator()
        v.add_required_rule("broker_api_key")
        v.add_range_rule("max_position_pct", 0, 100)
        v.add_type_rule("strategies", list)

        config = {
            "max_position_pct": 150,  # Out of range
            "strategies": "ibs",  # Wrong type
        }

        report = v.validate(config)
        assert not report.passed
        assert report.errors >= 2


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: Cross-module integration
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

    def test_trade_journal_records_limit_breach(self):
        """Limit breach triggers trade journal entry."""
        from ops.trade_journal import TradeJournal, TradeJournalEntry
        from risk.limit_monitor import LimitConfig, LimitMonitor, LimitStatus

        journal = TradeJournal()
        mon = LimitMonitor()
        mon.add_limit(LimitConfig("heat", warn_threshold=40, hard_limit=50))

        result = mon.check_limit("heat", current_value=55)
        if result.status == LimitStatus.BREACH:
            journal.add_entry(TradeJournalEntry(
                trade_id="breach-001", timestamp=result.checked_at,
                strategy="risk_monitor", symbol="PORTFOLIO",
                side="buy", quantity=0, price=0, broker="system",
                status="breach_alert", tags=["limit_breach", result.name],
            ))

        entries = journal.query(tags=["limit_breach"])
        assert len(entries) == 1
        assert entries[0].trade_id == "breach-001"

    def test_live_attribution_snapshot_to_health(self):
        """Live attribution feeds health dashboard."""
        from analytics.live_attribution import LiveAttributionEngine
        from app.health_dashboard import HealthDashboard, HealthStatus

        engine = LiveAttributionEngine(strategies=["ibs", "gtaa"])
        engine.update_pnl("ibs", unrealised=500, realised=200)
        engine.update_pnl("gtaa", unrealised=-100, realised=50)

        snap = engine.take_snapshot()
        dash = HealthDashboard()
        if snap.daily_pnl > 0:
            dash.update_status("pnl", HealthStatus.HEALTHY, f"Daily PnL: {snap.daily_pnl}")
        else:
            dash.update_status("pnl", HealthStatus.DEGRADED, f"Negative PnL: {snap.daily_pnl}")

        assert dash.get_report().overall_status == HealthStatus.HEALTHY

    def test_config_validator_validates_limit_config(self):
        """Config validator checks limit monitor configuration."""
        from ops.config_validator import ConfigValidator, ValidationSeverity

        validator = ConfigValidator()
        validator.add_range_rule("warn_threshold", 0, 100)
        validator.add_range_rule("hard_limit", 0, 100)
        validator.add_cross_ref_rule(
            "warn_threshold", "hard_limit",
            lambda w, h: w < h,
            "warn_threshold must be less than hard_limit",
        )

        good = {"warn_threshold": 40, "hard_limit": 50}
        report = validator.validate(good)
        assert report.passed

        bad = {"warn_threshold": 60, "hard_limit": 50}
        report = validator.validate(bad)
        assert not report.passed

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
        "ops/trade_journal.py",
        "analytics/live_attribution.py",
        "ops/config_validator.py",
    ]

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_source_file_exists(self, rel_path):
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full_path), f"Missing: {rel_path}"
