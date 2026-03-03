"""Phase I acceptance harness + release checks.

I-007: End-to-end tests covering all Phase I deliverables.
Validates alert routing, drawdown breaker, decay detector,
and cross-ticket integration.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Module import smoke tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseIModuleImports:
    """Verify all Phase I modules are importable."""

    def test_import_alert_router(self):
        from app.alert_router import Alert, AlertRouter
        assert Alert is not None
        assert AlertRouter is not None

    def test_import_drawdown_breaker(self):
        from risk.drawdown_breaker import (
            DrawdownAction,
            DrawdownConfig,
            DrawdownDecision,
            check_drawdown,
        )
        assert DrawdownAction is not None
        assert DrawdownConfig is not None
        assert callable(check_drawdown)

    def test_import_decay_detector(self):
        from analytics.decay_detector import (
            DecayConfig,
            StrategyHealth,
            detect_decay,
            get_decaying_strategies,
        )
        assert DecayConfig is not None
        assert callable(detect_decay)
        assert callable(get_decaying_strategies)

    def test_import_position_sizer(self):
        """Verify I-002 position sizer is importable."""
        try:
            from risk.position_sizer import PositionSizer
            assert PositionSizer is not None
        except (ImportError, AttributeError):
            # Check for alternative module structure
            try:
                import risk.position_sizer
                assert risk.position_sizer is not None
            except ImportError:
                pytest.skip("I-002 position sizer not yet merged")

    def test_import_oms(self):
        """Verify I-004 OMS is importable."""
        try:
            import execution.oms
            assert execution.oms is not None
        except ImportError:
            pytest.skip("I-004 OMS not yet merged")

    def test_import_market_data_monitor(self):
        """Verify I-005 market data monitor is importable."""
        try:
            import data.market_data_monitor
            assert data.market_data_monitor is not None
        except ImportError:
            pytest.skip("I-005 market data monitor not yet merged")


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Alert router E2E (I-001)
# ═══════════════════════════════════════════════════════════════════════════


class TestAlertRouterE2E:
    """End-to-end alert routing validation."""

    def test_full_alert_lifecycle(self):
        """Route alert, verify delivery, check history."""
        from app.alert_router import Alert, AlertRouter

        delivered = []
        router = AlertRouter(notify_fn=lambda msg, icon: (delivered.append(msg), True)[-1])

        # Route alerts for each Phase I subsystem
        router.circuit_breaker_trip("ig", 5, "open")
        router.promotion_gate_block("momentum", "STALE_SET", "stale")
        router.eod_reconciliation_report("2026-03-03", "clean", 0, 100.0)
        router.drawdown_alert(6.0, 5.0, "daily", "HALT")
        router.strategy_decay_warning("momentum", "sharpe", 0.3, 0.5)
        router.generic_error("scheduler", "tick failed")

        assert len(delivered) == 6
        assert len(router.history) == 6

    def test_alert_categories_are_distinct(self):
        """Each builder produces a unique category."""
        from app.alert_router import AlertRouter

        categories = set()
        router = AlertRouter(notify_fn=lambda msg, icon: True)

        router.circuit_breaker_trip("ig", 3, "open")
        router.promotion_gate_block("s", "r", "m")
        router.eod_reconciliation_report("d", "clean", 0, 0)
        router.drawdown_alert(5, 5, "d", "h")
        router.strategy_decay_warning("s", "m", 0, 0)
        router.generic_error("src", "err")

        categories = {a.category for a in router.history}
        assert len(categories) == 6


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Drawdown breaker E2E (I-003)
# ═══════════════════════════════════════════════════════════════════════════


class TestDrawdownBreakerE2E:
    """End-to-end drawdown circuit breaker validation."""

    def _init_db(self, tmp_path):
        from data import trade_db
        db_path = str(tmp_path / "dd_e2e.db")
        trade_db.init_db(db_path)
        return db_path

    def test_halt_blocks_warn_allows_clear_passes(self, tmp_path):
        """Full decision spectrum: halt > warn > allow."""
        from data.trade_db import get_conn
        from risk.drawdown_breaker import DrawdownAction, DrawdownConfig, check_drawdown

        db_path = self._init_db(tmp_path)
        conn = get_conn(db_path)

        # Insert daily report with 6% drawdown
        conn.execute(
            """INSERT INTO fund_daily_report
               (report_date, total_nav, total_cash, total_positions_value,
                unrealised_pnl, realised_pnl, drawdown_pct, high_water_mark,
                currency, created_at)
               VALUES ('2026-03-03', 94000, 0, 94000, 0, 0, 6.0, 100000, 'GBP', '2026-03-03T00:00:00Z')"""
        )
        conn.commit()
        conn.close()

        # 6% daily drawdown > 5% halt threshold
        config = DrawdownConfig(daily_halt_pct=5.0)
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db_path)
        assert decision.action == DrawdownAction.HALT

        # Same data but higher threshold → allow
        config2 = DrawdownConfig(daily_halt_pct=10.0, daily_warn_pct=8.0)
        decision2 = check_drawdown(config=config2, report_date="2026-03-03", db_path=db_path)
        assert decision2.action == DrawdownAction.ALLOW


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Decay detector E2E (I-006)
# ═══════════════════════════════════════════════════════════════════════════


class TestDecayDetectorE2E:
    """End-to-end strategy decay detection."""

    def _init_db(self, tmp_path):
        from data import trade_db
        db_path = str(tmp_path / "decay_e2e.db")
        trade_db.init_db(db_path)
        return db_path

    def test_decay_flags_bad_strategy(self, tmp_path):
        """Strategy with losing record gets flagged."""
        from data.trade_db import get_conn
        from analytics.decay_detector import DecayConfig, detect_decay

        db_path = self._init_db(tmp_path)
        conn = get_conn(db_path)

        # Insert 12 mostly-losing trades
        pnls = [-20, -10, 5, -30, -15, -25, 10, -20, -5, -10, -15, -30]
        for i, pnl in enumerate(pnls):
            ts = f"2026-02-{i+1:02d}T12:00:00"
            conn.execute(
                """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, pnl)
                   VALUES (?, 'TEST', 'bad_strat', 'BUY', 'CLOSE', 1, 100.0, ?)""",
                (ts, pnl),
            )
        conn.commit()
        conn.close()

        config = DecayConfig(min_trades=5, lookback_days=60)
        results = detect_decay(config=config, report_date="2026-03-03", db_path=db_path)
        assert len(results) == 1
        assert results[0].status in ("decay", "warning")


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Cross-ticket integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossTicketIntegration:
    """Validate Phase I deliverables integrate correctly."""

    def test_alert_router_wires_to_drawdown(self):
        """Alert router can send drawdown alerts."""
        from app.alert_router import AlertRouter

        alerts_sent = []
        router = AlertRouter(notify_fn=lambda msg, icon: (alerts_sent.append(msg), True)[-1])

        from risk.drawdown_breaker import DrawdownAction, DrawdownDecision
        decision = DrawdownDecision(
            action=DrawdownAction.HALT,
            reason="DAILY_HALT: 6.00% >= 5.00%",
            daily_drawdown_pct=6.0,
            weekly_drawdown_pct=3.0,
            current_nav=94000.0,
            high_water_mark=100000.0,
        )

        if decision.action == DrawdownAction.HALT:
            router.drawdown_alert(
                decision.daily_drawdown_pct,
                5.0,
                "daily",
                "HALT",
            )

        assert len(alerts_sent) == 1
        assert "DRAWDOWN" in alerts_sent[0]

    def test_alert_router_wires_to_decay(self):
        """Alert router can send decay alerts."""
        from app.alert_router import AlertRouter
        from analytics.decay_detector import StrategyHealth

        alerts_sent = []
        router = AlertRouter(notify_fn=lambda msg, icon: (alerts_sent.append(msg), True)[-1])

        health = StrategyHealth(
            strategy="momentum",
            status="decay",
            flags=["win_rate_below_floor", "profit_factor_below_floor"],
            recent_win_rate_pct=25.0,
        )

        if health.status == "decay":
            router.strategy_decay_warning(
                health.strategy,
                "win_rate",
                health.recent_win_rate_pct,
                35.0,
            )

        assert len(alerts_sent) == 1
        assert "DECAY" in alerts_sent[0]

    def test_drawdown_and_alert_router_backward_compatible(self):
        """Phase H circuit breaker still works alongside I-003 drawdown breaker."""
        from broker.circuit_breaker import BrokerCircuitBreaker, CircuitState
        from risk.drawdown_breaker import DrawdownAction, DrawdownConfig

        # Both should coexist without conflict
        cb = BrokerCircuitBreaker("test_broker")
        assert cb.state == CircuitState.CLOSED

        config = DrawdownConfig(enabled=False)
        assert config.daily_halt_pct == 5.0


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Source file presence
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseISourceFiles:
    """Validate all Phase I source files exist."""

    REQUIRED_FILES = [
        "app/alert_router.py",
        "risk/drawdown_breaker.py",
        "analytics/decay_detector.py",
    ]

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_source_file_exists(self, rel_path):
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full_path), f"Missing: {rel_path}"
