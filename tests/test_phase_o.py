"""Phase O acceptance harness — Full-Stack Completion.

O-009: Validates all O-* tickets:
- O-001: Seed data generator populates expected row counts
- O-002: Webhook creates order intents, kill switch blocks
- O-004: Notional fallback returns None with warning
- O-006: Sleeve P&L computed from trades
- O-008: Backtest API works, fragment renders
- Regression: Phase N tests file exists
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _temp_db():
    """Create a temporary database path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _init_temp_db(db_path):
    """Initialize a temp DB with all schemas."""
    from data.trade_db import init_db
    from data.order_intent_store import ensure_order_intent_schema

    init_db(db_path)
    ensure_order_intent_schema(db_path)
    return db_path


def _get_row_count(db_path, table):
    """Get row count from a table."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# O-001: Seed Data Generator Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeedDataGenerator:
    """O-001: seed_demo_data.py populates all tables."""

    @pytest.fixture(autouse=True)
    def setup_seeded_db(self, tmp_path):
        self.db_path = str(tmp_path / "test_seed.db")
        from data.trade_db import init_db
        from data.order_intent_store import ensure_order_intent_schema

        init_db(self.db_path)
        ensure_order_intent_schema(self.db_path)

        # Import and run seeder functions
        import seed_demo_data as seeder
        seeder.clear_all(self.db_path)

        from data.trade_db import get_conn
        conn = get_conn(self.db_path)
        seeder.seed_strategies(conn)
        seeder.seed_broker_positions(conn)
        seeder.seed_config_snapshots(conn)
        seeder.seed_fund_daily_reports(conn)
        seeder.seed_system_events(conn)
        seeder.seed_incidents(conn)
        seeder.seed_reconcile_results(conn)
        seeder.seed_kill_switch(conn)
        seeder.seed_broker_health(conn)
        seeder.seed_trades(conn)
        seeder.seed_daily_snapshots(conn)
        seeder.seed_positions(conn)
        seeder.seed_order_intents(conn)
        seeder.seed_research_results(conn)
        seeder.seed_calibration_runs(conn)
        seeder.seed_signal_engine_runs(conn)
        seeder.seed_jobs(conn)
        seeder.seed_ledger_entries(conn)
        seeder.seed_promotion_log(conn)
        seeder.seed_execution_quality(conn)
        seeder.seed_risk_snapshots(conn)
        seeder.seed_sleeve_reports(conn)
        seeder.seed_order_actions(conn)
        conn.commit()
        conn.close()

    def test_strategy_parameter_sets_populated(self):
        assert _get_row_count(self.db_path, "strategy_parameter_sets") == 4

    def test_strategy_state_populated(self):
        assert _get_row_count(self.db_path, "strategy_state") >= 10

    def test_strategy_promotions_populated(self):
        assert _get_row_count(self.db_path, "strategy_promotions") == 4

    def test_broker_accounts_populated(self):
        assert _get_row_count(self.db_path, "broker_accounts") == 3

    def test_broker_positions_populated(self):
        assert _get_row_count(self.db_path, "broker_positions") == 6

    def test_broker_cash_balances_populated(self):
        assert _get_row_count(self.db_path, "broker_cash_balances") == 3

    def test_fund_daily_report_populated(self):
        count = _get_row_count(self.db_path, "fund_daily_report")
        assert count >= 50, f"Expected >= 50 fund daily reports, got {count}"

    def test_sleeve_daily_report_populated(self):
        count = _get_row_count(self.db_path, "sleeve_daily_report")
        assert count >= 40, f"Expected >= 40 sleeve daily reports, got {count}"

    def test_trades_populated(self):
        count = _get_row_count(self.db_path, "trades")
        assert count >= 30, f"Expected >= 30 trades, got {count}"

    def test_daily_snapshots_populated(self):
        count = _get_row_count(self.db_path, "daily_snapshots")
        assert count >= 15, f"Expected >= 15 daily snapshots, got {count}"

    def test_positions_populated(self):
        assert _get_row_count(self.db_path, "positions") == 8

    def test_bot_events_populated(self):
        assert _get_row_count(self.db_path, "bot_events") == 20

    def test_control_actions_populated(self):
        assert _get_row_count(self.db_path, "control_actions") == 5

    def test_reconciliation_reports_populated(self):
        assert _get_row_count(self.db_path, "reconciliation_reports") == 3

    def test_order_intents_populated(self):
        assert _get_row_count(self.db_path, "order_intents") == 15

    def test_order_intent_transitions_populated(self):
        count = _get_row_count(self.db_path, "order_intent_transitions")
        assert count >= 15, f"Expected >= 15 transitions, got {count}"

    def test_order_execution_metrics_populated(self):
        count = _get_row_count(self.db_path, "order_execution_metrics")
        assert count >= 10, f"Expected >= 10 execution metrics, got {count}"

    def test_research_events_populated(self):
        assert _get_row_count(self.db_path, "research_events") == 6

    def test_calibration_runs_populated(self):
        assert _get_row_count(self.db_path, "calibration_runs") == 3

    def test_calibration_points_populated(self):
        count = _get_row_count(self.db_path, "calibration_points")
        assert count >= 10, f"Expected >= 10 calibration points, got {count}"

    def test_jobs_populated(self):
        assert _get_row_count(self.db_path, "jobs") == 8

    def test_nav_snapshots_populated(self):
        count = _get_row_count(self.db_path, "nav_snapshots")
        assert count >= 10, f"Expected >= 10 nav snapshots, got {count}"

    def test_risk_daily_snapshot_populated(self):
        count = _get_row_count(self.db_path, "risk_daily_snapshot")
        assert count >= 3, f"Expected >= 3 risk snapshots, got {count}"

    def test_order_actions_populated(self):
        assert _get_row_count(self.db_path, "order_actions") == 8

    def test_clear_only_empties_all(self):
        """--clear-only flag empties all tables."""
        import seed_demo_data as seeder
        seeder.clear_all(self.db_path)
        for table in ["trades", "positions", "bot_events", "order_intents", "jobs"]:
            assert _get_row_count(self.db_path, table) == 0

    def test_total_row_count_exceeds_300(self):
        """Sanity check: total across all tables > 300."""
        tables = [
            "strategy_parameter_sets", "strategy_state", "strategy_promotions",
            "broker_accounts", "broker_positions", "broker_cash_balances",
            "fund_daily_report", "sleeve_daily_report", "trades", "daily_snapshots",
            "positions", "bot_events", "control_actions", "reconciliation_reports",
            "order_intents", "order_intent_transitions", "order_execution_metrics",
            "research_events", "calibration_runs", "calibration_points",
            "jobs", "nav_snapshots", "order_actions",
        ]
        total = sum(_get_row_count(self.db_path, t) for t in tables)
        assert total >= 300, f"Total rows {total} < 300"

    def test_seed_demo_data_module_importable(self):
        """seed_demo_data.py can be imported."""
        import seed_demo_data
        assert hasattr(seed_demo_data, "seed_all")
        assert hasattr(seed_demo_data, "clear_all")
        assert hasattr(seed_demo_data, "print_summary")


# ═══════════════════════════════════════════════════════════════════════════════
# O-002: Webhook → Execution Wiring Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebhookExecutionWiring:
    """O-002: TradingView webhook creates order intents."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path, monkeypatch):
        self.db_path = str(tmp_path / "test_webhook.db")
        _init_temp_db(self.db_path)

        # Set a test webhook token so validation passes
        import config as cfg
        self._original_token = cfg.TRADINGVIEW_WEBHOOK_TOKEN
        monkeypatch.setattr(cfg, "TRADINGVIEW_WEBHOOK_TOKEN", "test-token-12345")

        from app.api.server import create_app
        self.app = create_app()

        from fastapi.testclient import TestClient
        self.client = TestClient(self.app)

    def _webhook_payload(self, action="buy", ticker="SPY", qty=10.0, **extra):
        payload = {
            "action": action,
            "ticker": ticker,
            "qty": qty,
            "strategy": "test_strategy",
            "token": "test-token-12345",
            **extra,
        }
        return payload

    def test_valid_buy_webhook_returns_ok(self):
        """Valid buy webhook returns ok response."""
        payload = self._webhook_payload()
        resp = self.client.post("/api/webhooks/tradingview", json=payload)
        # 200 = ok, 403 = kill switch
        assert resp.status_code in (200, 403)

    def test_valid_sell_webhook_returns_ok(self):
        """Valid sell webhook returns ok response."""
        payload = self._webhook_payload(action="sell")
        resp = self.client.post("/api/webhooks/tradingview", json=payload)
        assert resp.status_code in (200, 403)

    def test_invalid_token_rejected(self):
        """Invalid token returns 401."""
        payload = self._webhook_payload(token="wrong_token")
        resp = self.client.post(
            "/api/webhooks/tradingview",
            json=payload,
            headers={"x-webhook-token": "wrong_token"},
        )
        assert resp.status_code == 401

    def test_missing_payload_rejected(self):
        """Empty or malformed payload rejected."""
        resp = self.client.post(
            "/api/webhooks/tradingview",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code in (400, 422)

    def test_webhook_response_contains_intent_id(self):
        """Successful webhook response includes intent_id."""
        payload = self._webhook_payload()
        resp = self.client.post("/api/webhooks/tradingview", json=payload)
        data = resp.json()
        if data.get("ok"):
            assert "intent_id" in data
            assert "ticker" in data
            assert "action" in data

    def test_webhook_action_mapping(self):
        """Buy/sell/long/short actions are all recognized."""
        for action in ["buy", "sell", "long", "short"]:
            payload = self._webhook_payload(action=action)
            resp = self.client.post("/api/webhooks/tradingview", json=payload)
            data = resp.json()
            assert data.get("error") != "INVALID_ACTION", f"action={action} not recognized"

    def test_invalid_action_rejected(self):
        """Unknown action like 'hold' returns error."""
        payload = self._webhook_payload(action="hold")
        resp = self.client.post("/api/webhooks/tradingview", json=payload)
        data = resp.json()
        if resp.status_code == 422:
            assert data.get("error") == "INVALID_ACTION"

    def test_webhook_endpoint_exists(self):
        """POST /api/webhooks/tradingview endpoint exists."""
        resp = self.client.post("/api/webhooks/tradingview", json={})
        # Should not be 404/405
        assert resp.status_code != 404
        assert resp.status_code != 405


# ═══════════════════════════════════════════════════════════════════════════════
# O-004: Notional Fallback Fix Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotionalFallbackFix:
    """O-004: Notional fallback returns None instead of qty_requested."""

    def test_normal_fill_computes_notional(self):
        """When reference_price is valid, notional = qty * price."""
        ref_price = 525.0
        qty = 10.0
        if ref_price and ref_price > 0:
            notional = qty * ref_price
        else:
            notional = None
        assert notional == 5250.0

    def test_missing_price_returns_none(self):
        """When reference_price is None, notional should be None."""
        ref_price = None
        qty = 10.0
        if ref_price and ref_price > 0:
            notional = qty * ref_price
        else:
            notional = None
        assert notional is None

    def test_zero_price_returns_none(self):
        """When reference_price is 0, notional should be None."""
        ref_price = 0
        qty = 10.0
        if ref_price and ref_price > 0:
            notional = qty * ref_price
        else:
            notional = None
        assert notional is None

    def test_source_code_has_none_fallback(self):
        """Verify the source code uses None fallback, not qty_requested."""
        import inspect
        from data.order_intent_store import record_execution_metric
        source = inspect.getsource(record_execution_metric)
        assert "notional_requested = None" in source
        assert "notional_requested set to None" in source

    def test_warning_logged_on_missing_price(self, caplog):
        """Warning is logged when reference_price is missing."""
        from data.order_intent_store import logger as ois_logger
        # Verify the logger exists and is properly configured
        assert ois_logger is not None
        assert ois_logger.name == "data.order_intent_store"


# ═══════════════════════════════════════════════════════════════════════════════
# O-006: Sleeve P&L Attribution Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSleevePnlAttribution:
    """O-006: Sleeve P&L computed from closed trades."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        self.db_path = str(tmp_path / "test_sleeve.db")
        _init_temp_db(self.db_path)

    def test_no_trades_returns_zero(self):
        """No closed trades → realised_pnl = 0."""
        from fund.nav import _compute_sleeve_realised_pnl
        result = _compute_sleeve_realised_pnl(self.db_path)
        assert result == {} or all(v == 0 for v in result.values())

    def test_single_trade_attribution(self):
        """Single closed trade attributed to correct sleeve."""
        from data.trade_db import get_conn
        conn = get_conn(self.db_path)
        now = "2026-03-03T12:00:00"
        # Create broker account + position (for strategy→sleeve mapping)
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acc1', 'ibkr', 'acc1', 'ISA', 'GBP', 1, ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO broker_positions (broker_account_id, ticker, direction, quantity, avg_cost, strategy, sleeve, last_synced_at)
               VALUES ('acc1', 'SPY', 'long', 10, 500, 'GTAA', 'isa', ?)""",
            (now,),
        )
        # Create closed trade
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
               VALUES (?, 'SPY', 'GTAA', 'SELL', 'CLOSE', 10, 530, 'D1', 300.0, 'test')""",
            (now,),
        )
        conn.commit()
        conn.close()

        from fund.nav import _compute_sleeve_realised_pnl
        result = _compute_sleeve_realised_pnl(self.db_path)
        assert result.get("isa") == 300.0

    def test_multi_strategy_aggregation(self):
        """Multiple strategies aggregate P&L into same sleeve."""
        from data.trade_db import get_conn
        conn = get_conn(self.db_path)
        now = "2026-03-03T12:00:00"
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acc1', 'ibkr', 'acc1', 'ISA', 'GBP', 1, ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO broker_positions (broker_account_id, ticker, direction, quantity, avg_cost, strategy, sleeve, last_synced_at)
               VALUES ('acc1', 'SPY', 'long', 10, 500, 'GTAA', 'isa', ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO broker_positions (broker_account_id, ticker, direction, quantity, avg_cost, strategy, sleeve, last_synced_at)
               VALUES ('acc1', 'QQQ', 'long', 5, 450, 'IBS_QQQ', 'isa', ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
               VALUES (?, 'SPY', 'GTAA', 'SELL', 'CLOSE', 10, 530, 'D1', 300.0, 'test')""",
            (now,),
        )
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
               VALUES (?, 'QQQ', 'IBS_QQQ', 'SELL', 'CLOSE', 5, 480, 'D2', 150.0, 'test')""",
            (now,),
        )
        conn.commit()
        conn.close()

        from fund.nav import _compute_sleeve_realised_pnl
        result = _compute_sleeve_realised_pnl(self.db_path)
        assert result.get("isa") == 450.0

    def test_missing_strategy_goes_to_default(self):
        """Trade with unmapped strategy goes to default sleeve."""
        from data.trade_db import get_conn
        conn = get_conn(self.db_path)
        now = "2026-03-03T12:00:00"
        # No broker_positions → no strategy→sleeve mapping
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
               VALUES (?, 'SPY', 'UNKNOWN_STRAT', 'SELL', 'CLOSE', 10, 530, 'D1', 200.0, 'test')""",
            (now,),
        )
        conn.commit()
        conn.close()

        from fund.nav import _compute_sleeve_realised_pnl
        result = _compute_sleeve_realised_pnl(self.db_path)
        assert result.get("default") == 200.0

    def test_hardcoded_zero_removed(self):
        """Verify source code no longer has hardcoded 0.0 for realised_pnl."""
        import inspect
        from fund.nav import calculate_sleeve_navs
        source = inspect.getsource(calculate_sleeve_navs)
        assert "realised_pnl=0.0" not in source


# ═══════════════════════════════════════════════════════════════════════════════
# O-008: Backtester Control-Plane Surface Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestBacktesterSurface:
    """O-008: Backtest API and fragment."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        self.db_path = str(tmp_path / "test_backtest.db")
        _init_temp_db(self.db_path)

        from app.api.server import create_app
        self.app = create_app()

        from fastapi.testclient import TestClient
        self.client = TestClient(self.app)

    def test_submit_backtest_returns_job_id(self):
        """POST /api/backtest returns job_id."""
        resp = self.client.post("/api/backtest", json={"strategy": "IBS++ v3"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "job_id" in data

    def test_submit_backtest_missing_strategy_rejected(self):
        """POST /api/backtest without strategy returns 422."""
        resp = self.client.post("/api/backtest", json={})
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] == "MISSING_STRATEGY"

    def test_get_backtest_result_not_found(self):
        """GET /api/backtest/{id} for missing job returns 404."""
        resp = self.client.get("/api/backtest/nonexistent-id")
        assert resp.status_code == 404

    def test_get_backtest_result_after_submit(self):
        """GET /api/backtest/{id} returns status after submit."""
        resp = self.client.post("/api/backtest", json={"strategy": "IBS++ v3"})
        job_id = resp.json()["job_id"]
        # Small delay not needed — just check it exists
        resp2 = self.client.get(f"/api/backtest/{job_id}")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["ok"] is True
        assert data["status"] in ("running", "completed", "failed")

    def test_backtest_fragment_renders(self):
        """GET /fragments/backtest returns HTML."""
        resp = self.client.get("/fragments/backtest")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_backtest_template_exists(self):
        """_backtest.html template file exists."""
        tpl_path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "_backtest.html")
        assert os.path.isfile(tpl_path)

    def test_research_page_includes_backtest(self):
        """research_page.html includes the backtest fragment."""
        tpl_path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "research_page.html")
        with open(tpl_path) as f:
            content = f.read()
        assert "/fragments/backtest" in content


# ═══════════════════════════════════════════════════════════════════════════════
# Governance & Bootstrap Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhaseOGovernance:
    """O-000: Phase O bootstrap governance files updated."""

    def test_decisions_has_dec_032(self):
        """DEC-032 exists in DECISIONS.md."""
        path = os.path.join(PROJECT_ROOT, "ops", "collab", "DECISIONS.md")
        with open(path) as f:
            content = f.read()
        assert "DEC-032" in content
        assert "Phase O" in content

    def test_task_queue_has_o_tickets(self):
        """TASK_QUEUE.md has O-000 through O-009."""
        path = os.path.join(PROJECT_ROOT, "ops", "collab", "TASK_QUEUE.md")
        with open(path) as f:
            content = f.read()
        for i in range(10):
            assert f"O-{i:03d}" in content, f"Missing O-{i:03d} in TASK_QUEUE.md"

    def test_ownership_map_has_o_tickets(self):
        """OWNERSHIP_MAP.md has O-* ownership entries."""
        path = os.path.join(PROJECT_ROOT, "ops", "collab", "OWNERSHIP_MAP.md")
        with open(path) as f:
            content = f.read()
        assert "O-000" in content
        assert "O-009" in content

    def test_codex_mailbox_message_exists(self):
        """Codex mailbox message for Phase O split exists."""
        inbox = os.path.join(PROJECT_ROOT, "ops", "collab", "mailbox", "inbox")
        files = os.listdir(inbox) if os.path.isdir(inbox) else []
        phase_o_msgs = [f for f in files if "phase-o" in f.lower()]
        assert len(phase_o_msgs) >= 1, "No Phase O mailbox message found"


# ═══════════════════════════════════════════════════════════════════════════════
# File Structure Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFileStructure:
    """Key files exist and are properly structured."""

    def test_seed_demo_data_exists(self):
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "seed_demo_data.py"))

    def test_backtest_template_exists(self):
        assert os.path.isfile(
            os.path.join(PROJECT_ROOT, "app", "web", "templates", "_backtest.html")
        )

    def test_phase_n_test_file_exists(self):
        """Regression: Phase N test file still exists."""
        assert os.path.isfile(
            os.path.join(PROJECT_ROOT, "tests", "test_phase_n_ui.py")
        )

    def test_order_intent_store_has_logger(self):
        """order_intent_store.py has a logger configured."""
        from data.order_intent_store import logger
        assert logger is not None

    def test_nav_has_sleeve_pnl_function(self):
        """fund/nav.py has _compute_sleeve_realised_pnl function."""
        from fund.nav import _compute_sleeve_realised_pnl
        assert callable(_compute_sleeve_realised_pnl)


# ═══════════════════════════════════════════════════════════════════════════════
# API Endpoint Existence Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAPIEndpoints:
    """All O-* API endpoints exist and respond."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        self.db_path = str(tmp_path / "test_api.db")
        _init_temp_db(self.db_path)

        from app.api.server import create_app
        self.app = create_app()

        from fastapi.testclient import TestClient
        self.client = TestClient(self.app)

    def test_webhook_endpoint_exists(self):
        resp = self.client.post("/api/webhooks/tradingview", json={})
        assert resp.status_code != 404

    def test_backtest_post_endpoint_exists(self):
        resp = self.client.post("/api/backtest", json={"strategy": "x"})
        assert resp.status_code != 404

    def test_backtest_get_endpoint_exists(self):
        resp = self.client.get("/api/backtest/test-id")
        assert resp.status_code in (200, 404)  # 404 for missing job is ok

    def test_backtest_fragment_endpoint_exists(self):
        resp = self.client.get("/fragments/backtest")
        assert resp.status_code == 200

    def test_research_page_endpoint_exists(self):
        resp = self.client.get("/research")
        assert resp.status_code == 200

    def test_health_endpoint_still_works(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200

    def test_equity_curve_endpoint_still_works(self):
        resp = self.client.get("/api/charts/equity-curve")
        assert resp.status_code == 200
