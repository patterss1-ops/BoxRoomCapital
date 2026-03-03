"""Tests for L-005 notification template engine."""

from __future__ import annotations

import pytest

from app.notification_templates import (
    NotificationChannel,
    NotificationSeverity,
    NotificationTemplate,
    NotificationTemplateEngine,
    RenderedNotification,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def engine() -> NotificationTemplateEngine:
    return NotificationTemplateEngine()


TRADE_VARS = {"side": "BUY", "qty": 100, "ticker": "AAPL", "price": 185.50}
RISK_VARS = {"alert_type": "VAR_BREACH", "message": "Portfolio VaR exceeded limit"}
DRAWDOWN_VARS = {"strategy": "momentum", "drawdown_pct": 7.2}
SIGNAL_VARS = {"verdict": "BUY", "ticker": "MSFT", "score": 0.85}
HEALTH_VARS = {"component": "broker_api", "status": "degraded"}
REBALANCE_VARS = {"strategy": "gtaa", "drift_pct": 3.5}


# ─── Test: render built-in templates ───────────────────────────────────────

class TestRenderBuiltins:
    def test_render_trade_executed(self, engine: NotificationTemplateEngine):
        results = engine.render("trade_executed", TRADE_VARS, channel=NotificationChannel.LOG)
        assert len(results) == 1
        r = results[0]
        assert r.template_name == "trade_executed"
        assert "BUY" in r.subject
        assert "100" in r.subject
        assert "AAPL" in r.subject
        assert "185.5" in r.subject

    def test_render_risk_alert(self, engine: NotificationTemplateEngine):
        results = engine.render("risk_alert", RISK_VARS, channel=NotificationChannel.LOG)
        assert len(results) == 1
        assert "VAR_BREACH" in results[0].body
        assert results[0].severity == NotificationSeverity.CRITICAL

    def test_render_all_builtin_templates(self, engine: NotificationTemplateEngine):
        """Every built-in template renders without error when given correct vars."""
        template_vars = {
            "trade_executed": TRADE_VARS,
            "risk_alert": RISK_VARS,
            "drawdown_warning": DRAWDOWN_VARS,
            "signal_generated": SIGNAL_VARS,
            "system_health": HEALTH_VARS,
            "rebalance_triggered": REBALANCE_VARS,
        }
        for name in engine.list_templates():
            results = engine.render(name, template_vars[name], channel=NotificationChannel.LOG)
            assert len(results) == 1
            assert results[0].template_name == name


# ─── Test: custom template registration ───────────────────────────────────

class TestRegistration:
    def test_register_custom_template(self, engine: NotificationTemplateEngine):
        custom = NotificationTemplate(
            name="custom_alert",
            subject_template="Custom: {msg}",
            body_template="Detail: {msg}",
            severity=NotificationSeverity.INFO,
            channels=[NotificationChannel.LOG],
            required_vars=["msg"],
        )
        engine.register(custom)
        assert engine.get_template("custom_alert") is not None
        results = engine.render("custom_alert", {"msg": "hello"})
        assert len(results) == 1
        assert "hello" in results[0].body

    def test_duplicate_registration_raises(self, engine: NotificationTemplateEngine):
        custom = NotificationTemplate(
            name="trade_executed",
            subject_template="dup",
            body_template="dup",
            severity=NotificationSeverity.INFO,
        )
        with pytest.raises(ValueError, match="already registered"):
            engine.register(custom)


# ─── Test: validation ─────────────────────────────────────────────────────

class TestValidation:
    def test_validate_detects_missing_variables(self, engine: NotificationTemplateEngine):
        missing = engine.validate("trade_executed", {"side": "BUY"})
        assert "qty" in missing
        assert "ticker" in missing
        assert "price" in missing
        assert "side" not in missing

    def test_validate_passes_with_all_variables(self, engine: NotificationTemplateEngine):
        missing = engine.validate("trade_executed", TRADE_VARS)
        assert missing == []

    def test_validate_unknown_template_raises(self, engine: NotificationTemplateEngine):
        with pytest.raises(ValueError, match="Unknown template"):
            engine.validate("nonexistent", {})


# ─── Test: channel rendering ──────────────────────────────────────────────

class TestChannelRouting:
    def test_render_with_specific_channel_override(self, engine: NotificationTemplateEngine):
        results = engine.render("trade_executed", TRADE_VARS, channel=NotificationChannel.EMAIL)
        assert len(results) == 1
        assert results[0].channel == NotificationChannel.EMAIL

    def test_render_for_all_channels(self, engine: NotificationTemplateEngine):
        results = engine.render("trade_executed", TRADE_VARS)
        channels = {r.channel for r in results}
        assert NotificationChannel.TELEGRAM in channels
        assert NotificationChannel.EMAIL in channels
        assert NotificationChannel.LOG in channels
        assert len(results) == 3


# ─── Test: channel-specific formatting ─────────────────────────────────────

class TestChannelFormatting:
    def test_telegram_formatting_bold_header(self, engine: NotificationTemplateEngine):
        results = engine.render(
            "trade_executed", TRADE_VARS, channel=NotificationChannel.TELEGRAM,
        )
        r = results[0]
        assert r.subject.startswith("*")
        assert r.subject.endswith("*")
        # Body should NOT be wrapped
        assert not r.body.startswith("*")

    def test_email_formatting_html_wrapping(self, engine: NotificationTemplateEngine):
        results = engine.render(
            "trade_executed", TRADE_VARS, channel=NotificationChannel.EMAIL,
        )
        r = results[0]
        assert r.body.startswith("<p>")
        assert r.body.endswith("</p>")
        # Subject should NOT have HTML tags
        assert "<p>" not in r.subject

    def test_log_formatting_severity_prefix(self, engine: NotificationTemplateEngine):
        results = engine.render(
            "trade_executed", TRADE_VARS, channel=NotificationChannel.LOG,
        )
        r = results[0]
        assert r.body.startswith("[INFO]")
        assert ": " in r.body

    def test_log_formatting_critical_severity(self, engine: NotificationTemplateEngine):
        results = engine.render(
            "risk_alert", RISK_VARS, channel=NotificationChannel.LOG,
        )
        r = results[0]
        assert r.body.startswith("[CRITICAL]")


# ─── Test: bulk rendering ─────────────────────────────────────────────────

class TestBulkRendering:
    def test_render_bulk_multiple_items(self, engine: NotificationTemplateEngine):
        items = [
            ("trade_executed", TRADE_VARS),
            ("risk_alert", RISK_VARS),
        ]
        results = engine.render_bulk(items)
        # Each template targets 3 channels
        assert len(results) == 6
        names = {r.template_name for r in results}
        assert "trade_executed" in names
        assert "risk_alert" in names


# ─── Test: edge cases ─────────────────────────────────────────────────────

class TestEdgeCases:
    def test_get_nonexistent_template_returns_none(self, engine: NotificationTemplateEngine):
        assert engine.get_template("does_not_exist") is None

    def test_list_templates_returns_all_names(self, engine: NotificationTemplateEngine):
        names = engine.list_templates()
        assert "trade_executed" in names
        assert "risk_alert" in names
        assert "drawdown_warning" in names
        assert "signal_generated" in names
        assert "system_health" in names
        assert "rebalance_triggered" in names
        assert len(names) == 6

    def test_missing_template_in_render_raises(self, engine: NotificationTemplateEngine):
        with pytest.raises(ValueError, match="Unknown template"):
            engine.render("nonexistent", {})

    def test_extra_variables_are_ignored(self, engine: NotificationTemplateEngine):
        extended_vars = {**TRADE_VARS, "extra_field": "should be ignored"}
        results = engine.render("trade_executed", extended_vars, channel=NotificationChannel.LOG)
        assert len(results) == 1
        assert "extra_field" not in results[0].body

    def test_empty_variables_with_no_required_vars(self, engine: NotificationTemplateEngine):
        no_req = NotificationTemplate(
            name="no_vars_needed",
            subject_template="Static subject",
            body_template="Static body",
            severity=NotificationSeverity.INFO,
            channels=[NotificationChannel.LOG],
            required_vars=[],
        )
        engine.register(no_req)
        results = engine.render("no_vars_needed", {})
        assert len(results) == 1
        assert results[0].subject == "Static subject"

    def test_rendered_at_is_iso8601(self, engine: NotificationTemplateEngine):
        results = engine.render("trade_executed", TRADE_VARS, channel=NotificationChannel.LOG)
        rendered_at = results[0].rendered_at
        # ISO 8601 contains 'T' and '+' or 'Z' for UTC offset
        assert "T" in rendered_at

    def test_rendered_notification_severity_matches_template(
        self, engine: NotificationTemplateEngine,
    ):
        results = engine.render("risk_alert", RISK_VARS, channel=NotificationChannel.LOG)
        assert results[0].severity == NotificationSeverity.CRITICAL

    def test_list_templates_sorted(self, engine: NotificationTemplateEngine):
        names = engine.list_templates()
        assert names == sorted(names)
