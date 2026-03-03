"""Phase I acceptance harness + release checks.

I-007: End-to-end tests covering all Phase I deliverables.
Validates alert routing, position sizing, drawdown breaker, OMS,
market data monitor, decay detector, and cross-ticket integration.
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
        from risk.position_sizer import SizingConfig, compute_position_size
        assert SizingConfig is not None
        assert callable(compute_position_size)

    def test_import_oms(self):
        """Verify I-004 OMS is importable."""
        from execution.oms import OrderManager, OrderState
        assert OrderManager is not None
        assert OrderState is not None

    def test_import_market_data_monitor(self):
        """Verify I-005 market data monitor is importable."""
        from data.market_data_monitor import MarketDataMonitor, ProviderStatus
        assert MarketDataMonitor is not None
        assert ProviderStatus is not None


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
        "risk/position_sizer.py",
        "risk/limits_engine.py",
        "risk/drawdown_breaker.py",
        "execution/oms.py",
        "data/market_data_monitor.py",
        "analytics/decay_detector.py",
    ]

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_source_file_exists(self, rel_path):
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full_path), f"Missing: {rel_path}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Position sizer E2E (I-002)
# ═══════════════════════════════════════════════════════════════════════════


class TestPositionSizerE2E:
    """End-to-end position sizing validation."""

    def test_basic_sizing_returns_result(self):
        """Default config produces a valid sizing result."""
        from risk.position_sizer import SizingConfig, SizingContext, compute_position_size

        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=SizingConfig(),
            context=SizingContext(equity=100_000.0),
        )
        assert result.recommended_notional > 0
        assert result.ticker == "AAPL"
        assert result.strategy == "momentum"

    def test_zero_equity_blocks(self):
        """Zero equity produces zero sizing."""
        from risk.position_sizer import SizingConfig, SizingContext, compute_position_size

        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=SizingConfig(),
            context=SizingContext(equity=0.0),
        )
        assert result.recommended_notional == 0.0
        assert result.capped_by == "zero_equity"

    def test_volatility_adjustment(self):
        """Volatility-adjusted sizing differs from fixed."""
        from risk.position_sizer import SizingConfig, SizingContext, compute_position_size

        ctx_no_vol = SizingContext(equity=100_000.0)
        ctx_with_vol = SizingContext(equity=100_000.0, ticker_volatility_pct=40.0)

        r1 = compute_position_size("T", "s", 100.0, SizingConfig(), ctx_no_vol)
        r2 = compute_position_size("T", "s", 100.0, SizingConfig(), ctx_with_vol)
        assert r1.sizing_method == "fixed"
        assert r2.sizing_method == "volatility_adjusted"

    def test_position_sizer_class_wrapper(self):
        """PositionSizer class delegates to compute_position_size."""
        from risk.position_sizer import PositionSizer

        sizer = PositionSizer()
        result = sizer.size_position("MSFT", "mean_rev", 300.0)
        assert result.recommended_notional > 0


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: OMS E2E (I-004)
# ═══════════════════════════════════════════════════════════════════════════


class TestOmsE2E:
    """End-to-end order management validation."""

    def test_full_order_lifecycle(self):
        """PENDING → SUBMITTED → PARTIAL → FILLED."""
        from execution.oms import OrderManager, OrderState

        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 100, strategy="momentum")
        assert order.state == OrderState.PENDING

        mgr.submit(order.order_id, broker_ref="BR001")
        assert order.state == OrderState.SUBMITTED

        mgr.fill(order.order_id, 50, 150.0)
        assert order.state == OrderState.PARTIAL

        mgr.fill(order.order_id, 100, 151.0)
        assert order.state == OrderState.FILLED
        assert order.is_terminal

    def test_cancel_and_reject_paths(self):
        """Verify cancel and reject terminal states."""
        from execution.oms import OrderManager, OrderState

        mgr = OrderManager()
        o1 = mgr.create_order("GOOG", "SELL", 50)
        mgr.submit(o1.order_id)
        mgr.cancel(o1.order_id, reason="user request")
        assert o1.state == OrderState.CANCELLED

        o2 = mgr.create_order("TSLA", "BUY", 200)
        mgr.reject(o2.order_id, reason="margin")
        assert o2.state == OrderState.REJECTED

    def test_active_vs_terminal_filtering(self):
        """Active orders exclude terminal ones."""
        from execution.oms import OrderManager

        mgr = OrderManager()
        o1 = mgr.create_order("A", "BUY", 10)
        o2 = mgr.create_order("B", "SELL", 20)
        mgr.submit(o1.order_id)
        mgr.fill(o1.order_id, 10, 100.0)

        active = mgr.get_active_orders()
        assert len(active) == 1
        assert active[0].ticker == "B"


# ═══════════════════════════════════════════════════════════════════════════
# Section 9: Market data monitor E2E (I-005)
# ═══════════════════════════════════════════════════════════════════════════


class TestMarketDataMonitorE2E:
    """End-to-end market data monitoring validation."""

    def test_provider_health_lifecycle(self):
        """Provider degrades on failures, recovers on success."""
        from data.market_data_monitor import MarketDataMonitor, ProviderStatus

        mon = MarketDataMonitor(failure_threshold=2)
        assert mon.providers["yfinance"].status == ProviderStatus.HEALTHY

        mon.record_failure("yfinance")
        assert mon.providers["yfinance"].status == ProviderStatus.DEGRADED

        mon.record_failure("yfinance")
        assert mon.providers["yfinance"].status == ProviderStatus.DOWN

        mon.record_success("yfinance", ticker="AAPL")
        assert mon.providers["yfinance"].status == ProviderStatus.HEALTHY

    def test_freshness_tracking(self):
        """Data freshness is tracked per ticker."""
        from data.market_data_monitor import MarketDataMonitor

        mon = MarketDataMonitor()
        assert mon.check_freshness("AAPL").status == "missing"

        mon.record_success("yfinance", ticker="AAPL")
        check = mon.check_freshness("AAPL")
        assert check.is_fresh is True
        assert check.status == "ok"

    def test_provider_fallback(self):
        """Falls back to degraded provider when primary is down."""
        from data.market_data_monitor import MarketDataMonitor, ProviderStatus

        mon = MarketDataMonitor(providers=["primary", "backup"], failure_threshold=1)
        mon.record_failure("primary")
        assert mon.providers["primary"].status == ProviderStatus.DOWN

        best = mon.get_healthy_provider()
        assert best == "backup"

    def test_status_summary(self):
        """Summary reports provider and ticker counts."""
        from data.market_data_monitor import MarketDataMonitor

        mon = MarketDataMonitor(providers=["a", "b"])
        mon.record_success("a", ticker="X")
        mon.record_success("a", ticker="Y")
        summary = mon.get_status_summary()
        assert summary["total_providers"] == 2
        assert summary["tracked_tickers"] == 2
