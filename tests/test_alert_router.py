"""Tests for I-001 alert router."""

from __future__ import annotations

from app.alert_router import Alert, AlertRouter


class TestAlertDataClass:
    def test_alert_to_dict(self):
        a = Alert(
            category="circuit_breaker",
            severity="critical",
            title="TEST",
            message="test message",
        )
        d = a.to_dict()
        assert d["category"] == "circuit_breaker"
        assert d["severity"] == "critical"
        assert "timestamp" in d

    def test_alert_auto_timestamp(self):
        a = Alert(category="test", severity="info", title="T", message="M")
        assert a.timestamp != ""


class TestAlertRouter:
    def _make_router(self):
        sent = []

        def notify(message: str, icon: str) -> bool:
            sent.append({"message": message, "icon": icon})
            return True

        router = AlertRouter(notify_fn=notify)
        return router, sent

    def test_route_calls_notify_fn(self):
        router, sent = self._make_router()
        alert = Alert(category="test", severity="info", title="Hello", message="World")
        result = router.route(alert)
        assert result is True
        assert len(sent) == 1
        assert "Hello" in sent[0]["message"]

    def test_route_records_history(self):
        router, _ = self._make_router()
        router.route(Alert(category="test", severity="info", title="T", message="M"))
        assert len(router.history) == 1

    def test_no_notify_fn_returns_false(self):
        router = AlertRouter(notify_fn=None)
        result = router.route(
            Alert(category="test", severity="info", title="T", message="M")
        )
        assert result is False
        assert len(router.history) == 1  # still recorded

    def test_suppress_category(self):
        router, sent = self._make_router()
        router.suppress_category("test")
        result = router.route(
            Alert(category="test", severity="info", title="T", message="M")
        )
        assert result is False
        assert len(sent) == 0

    def test_unsuppress_category(self):
        router, sent = self._make_router()
        router.suppress_category("test")
        router.unsuppress_category("test")
        result = router.route(
            Alert(category="test", severity="info", title="T", message="M")
        )
        assert result is True
        assert len(sent) == 1

    def test_history_ring_buffer(self):
        router, _ = self._make_router()
        for i in range(150):
            router.route(Alert(category="test", severity="info", title=f"T{i}", message="M"))
        assert len(router.history) == 100  # capped at max_history


class TestConvenienceBuilders:
    def _make_router(self):
        sent = []

        def notify(message: str, icon: str) -> bool:
            sent.append({"message": message, "icon": icon})
            return True

        return AlertRouter(notify_fn=notify), sent

    def test_circuit_breaker_trip(self):
        router, sent = self._make_router()
        result = router.circuit_breaker_trip("ig", 5, "open")
        assert result is True
        assert "CIRCUIT BREAKER" in sent[0]["message"]
        assert "ig" in sent[0]["message"]
        assert sent[0]["icon"] == "🚨"

    def test_circuit_breaker_recovery(self):
        router, sent = self._make_router()
        result = router.circuit_breaker_recovery("ig")
        assert result is True
        assert "RECOVERED" in sent[0]["message"]

    def test_promotion_gate_block(self):
        router, sent = self._make_router()
        result = router.promotion_gate_block("momentum", "NO_LIVE_SET", "No live set found")
        assert result is True
        assert "PROMOTION GATE" in sent[0]["message"]

    def test_eod_reconciliation_report(self):
        router, sent = self._make_router()
        result = router.eod_reconciliation_report("2026-03-03", "clean", 0, 150.0)
        assert result is True
        assert "EOD REPORT" in sent[0]["message"]
        assert "+150.00" in sent[0]["message"]

    def test_eod_report_warning_severity(self):
        router, sent = self._make_router()
        router.eod_reconciliation_report("2026-03-03", "warning", 3, -50.0)
        assert sent[0]["icon"] == "⚠️"

    def test_drawdown_alert(self):
        router, sent = self._make_router()
        result = router.drawdown_alert(6.5, 5.0, "daily", "HALT")
        assert result is True
        assert "DRAWDOWN" in sent[0]["message"]
        assert "6.50%" in sent[0]["message"]

    def test_strategy_decay_warning(self):
        router, sent = self._make_router()
        result = router.strategy_decay_warning("momentum", "sharpe", 0.3, 0.5)
        assert result is True
        assert "DECAY" in sent[0]["message"]

    def test_generic_error(self):
        router, sent = self._make_router()
        result = router.generic_error("scheduler", "tick failed")
        assert result is True
        assert "ERROR" in sent[0]["message"]
        assert sent[0]["icon"] == "🚨"

    def test_notify_fn_exception_handled(self):
        def broken_notify(msg, icon):
            raise RuntimeError("boom")

        router = AlertRouter(notify_fn=broken_notify)
        result = router.circuit_breaker_trip("ig", 3, "open")
        assert result is False  # graceful failure
        assert len(router.history) == 1  # still recorded
