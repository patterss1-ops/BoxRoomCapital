"""
Tests for A-007: Control Plane Phase A surfaces.

Covers:
- New DB query functions (get_risk_verdicts, get_risk_verdict_summary)
- New JSON API endpoints (/api/broker/*, /api/nav/*, /api/risk/*, /api/reconciliation/*)
- New HTMX fragment endpoints (/fragments/broker-health, etc.)
- Fund page route (/fund)
- Navigation bar includes Fund link
"""
import json
import os
import tempfile

import pytest

from data.trade_db import (
    get_conn,
    get_risk_verdicts,
    get_risk_verdict_summary,
    init_db,
)
from execution.ledger import (
    register_broker_account,
    sync_positions,
    sync_cash_balance,
    save_nav_snapshot,
    get_broker_accounts,
    get_unified_positions,
    get_latest_cash_balances,
    get_nav_history,
    reconcile_positions,
    get_reconciliation_reports,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db_path():
    """Create a temporary DB for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def seeded_db(db_path):
    """DB with broker accounts, positions, cash, NAV, and risk verdicts."""
    # Register two broker accounts
    ig_id = register_broker_account(
        broker="ig",
        account_id="IG-SPREAD-001",
        account_type="SPREADBET",
        currency="GBP",
        label="IG Spreadbet",
        db_path=db_path,
    )
    ibkr_id = register_broker_account(
        broker="ibkr",
        account_id="DU12345",
        account_type="PAPER",
        currency="USD",
        label="IBKR Paper",
        db_path=db_path,
    )

    # Sync positions
    sync_positions(
        ig_id,
        [
            {"ticker": "FTSE100", "direction": "long", "quantity": 2, "avg_cost": 7500,
             "market_value": 15200, "unrealised_pnl": 200, "strategy": "ibs_credit_spreads",
             "sleeve": "sleeve_1"},
        ],
        db_path=db_path,
    )
    sync_positions(
        ibkr_id,
        [
            {"ticker": "SPY", "direction": "long", "quantity": 50, "avg_cost": 480,
             "market_value": 24500, "unrealised_pnl": 500, "currency": "USD",
             "strategy": "ibs_etf", "sleeve": "sleeve_2"},
            {"ticker": "QQQ", "direction": "long", "quantity": 30, "avg_cost": 420,
             "market_value": 12900, "unrealised_pnl": -300, "currency": "USD",
             "strategy": "ibs_etf", "sleeve": "sleeve_2"},
        ],
        db_path=db_path,
    )

    # Sync cash balances
    sync_cash_balance(ig_id, balance=25000.0, buying_power=50000.0, currency="GBP", db_path=db_path)
    sync_cash_balance(ibkr_id, balance=30000.0, buying_power=60000.0, currency="USD", db_path=db_path)

    # Save NAV snapshots
    save_nav_snapshot(
        level="fund", level_id="fund", net_liquidation=100000.0,
        cash=55000.0, positions_value=52600.0, unrealised_pnl=400.0,
        snapshot_date="2026-02-28", db_path=db_path,
    )
    save_nav_snapshot(
        level="fund", level_id="fund", net_liquidation=99500.0,
        cash=54000.0, positions_value=52100.0, unrealised_pnl=-500.0,
        snapshot_date="2026-02-27", db_path=db_path,
    )

    # Insert risk verdicts
    conn = get_conn(db_path)
    for i in range(5):
        approved = 1 if i < 3 else 0
        rule_id = None if approved else "R-007"
        reason = "OK" if approved else "Position concentration exceeded"
        conn.execute(
            """INSERT INTO risk_verdicts
               (id, created_at, ticker, direction, quantity, strategy, sleeve, broker,
                approved, rule_id, reason, checks_run, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"verdict-{i}",
                f"2026-02-28T14:{i:02d}:00",
                "SPY" if i < 3 else "AAPL",
                "long",
                10 + i,
                "ibs_etf",
                "sleeve_2",
                "ibkr",
                approved,
                rule_id,
                reason,
                12,
                json.dumps([{"rule_id": "R-001", "passed": True}]),
            ),
        )
    # Add one more rejection with different rule
    conn.execute(
        """INSERT INTO risk_verdicts
           (id, created_at, ticker, direction, quantity, strategy, sleeve, broker,
            approved, rule_id, reason, checks_run, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "verdict-5",
            "2026-02-28T14:05:00",
            "TSLA",
            "long",
            5,
            "ibs_etf",
            "sleeve_2",
            "ibkr",
            0,
            "R-005",
            "Trade risk exceeded",
            5,
            json.dumps([]),
        ),
    )
    conn.commit()
    conn.close()

    # Run a reconciliation
    reconcile_positions(
        ibkr_id,
        [
            {"ticker": "SPY", "direction": "long", "quantity": 50, "avg_cost": 480},
            {"ticker": "QQQ", "direction": "long", "quantity": 30, "avg_cost": 420},
        ],
        db_path=db_path,
    )

    return {
        "db_path": db_path,
        "ig_id": ig_id,
        "ibkr_id": ibkr_id,
    }


# ─── DB Query Tests ───────────────────────────────────────────────────────


class TestGetRiskVerdicts:
    """Tests for get_risk_verdicts() DB function."""

    def test_returns_all_verdicts(self, seeded_db):
        verdicts = get_risk_verdicts(db_path=seeded_db["db_path"])
        assert len(verdicts) == 6

    def test_filter_approved(self, seeded_db):
        approved = get_risk_verdicts(approved=1, db_path=seeded_db["db_path"])
        assert len(approved) == 3
        for v in approved:
            assert v["approved"] == 1

    def test_filter_rejected(self, seeded_db):
        rejected = get_risk_verdicts(approved=0, db_path=seeded_db["db_path"])
        assert len(rejected) == 3
        for v in rejected:
            assert v["approved"] == 0

    def test_filter_by_ticker(self, seeded_db):
        spy = get_risk_verdicts(ticker="SPY", db_path=seeded_db["db_path"])
        assert len(spy) == 3
        for v in spy:
            assert v["ticker"] == "SPY"

    def test_limit_works(self, seeded_db):
        limited = get_risk_verdicts(limit=2, db_path=seeded_db["db_path"])
        assert len(limited) == 2

    def test_details_parsed_as_list(self, seeded_db):
        verdicts = get_risk_verdicts(limit=1, db_path=seeded_db["db_path"])
        assert isinstance(verdicts[0]["details"], list)

    def test_ordered_by_created_at_desc(self, seeded_db):
        verdicts = get_risk_verdicts(db_path=seeded_db["db_path"])
        timestamps = [v["created_at"] for v in verdicts]
        assert timestamps == sorted(timestamps, reverse=True)


class TestGetRiskVerdictSummary:
    """Tests for get_risk_verdict_summary() DB function."""

    def test_summary_counts(self, seeded_db):
        summary = get_risk_verdict_summary(db_path=seeded_db["db_path"])
        assert summary["total"] == 6
        assert summary["approved"] == 3
        assert summary["rejected"] == 3

    def test_approval_rate(self, seeded_db):
        summary = get_risk_verdict_summary(db_path=seeded_db["db_path"])
        assert summary["approval_rate"] == 50.0

    def test_top_rejection_rules(self, seeded_db):
        summary = get_risk_verdict_summary(db_path=seeded_db["db_path"])
        rules = summary["top_rejection_rules"]
        assert len(rules) >= 1
        # R-007 should have 2 rejections, R-005 should have 1
        rule_ids = {r["rule_id"]: r["cnt"] for r in rules}
        assert rule_ids.get("R-007") == 2
        assert rule_ids.get("R-005") == 1

    def test_empty_db_summary(self, db_path):
        summary = get_risk_verdict_summary(db_path=db_path)
        assert summary["total"] == 0
        assert summary["approved"] == 0
        assert summary["rejected"] == 0
        assert summary["approval_rate"] == 0


# ─── FastAPI App Tests ────────────────────────────────────────────────────


@pytest.fixture
def app_client(seeded_db):
    """
    Create a test client for the FastAPI app.

    Patches get_conn at the module level in both data.trade_db and
    execution.ledger so all DB functions route to our seeded test DB.
    Default function parameters capture DB_PATH at import time, so we
    must patch the underlying connector instead.
    """
    from unittest.mock import patch
    import data.trade_db as tdb
    import execution.ledger as ledger_mod

    original_get_conn = tdb.get_conn

    def test_get_conn(db_path=None):
        """Always connect to the test DB."""
        return original_get_conn(seeded_db["db_path"])

    with patch.object(tdb, "get_conn", test_get_conn), \
         patch.object(ledger_mod, "get_conn", test_get_conn):
        from app.api.server import create_app
        from starlette.testclient import TestClient

        test_app = create_app()
        client = TestClient(test_app)
        yield client


class TestBrokerAccountsAPI:
    """Tests for /api/broker/accounts endpoint."""

    def test_returns_accounts(self, app_client):
        resp = app_client.get("/api/broker/accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 2

    def test_filter_by_broker(self, app_client):
        resp = app_client.get("/api/broker/accounts?broker=ig")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["broker"] == "ig"


class TestBrokerPositionsAPI:
    """Tests for /api/broker/positions endpoint."""

    def test_returns_all_positions(self, app_client):
        resp = app_client.get("/api/broker/positions")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3  # 1 IG + 2 IBKR

    def test_filter_by_broker(self, app_client):
        resp = app_client.get("/api/broker/positions?broker=ibkr")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2

    def test_filter_by_sleeve(self, app_client):
        resp = app_client.get("/api/broker/positions?sleeve=sleeve_1")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["ticker"] == "FTSE100"


class TestBrokerCashAPI:
    """Tests for /api/broker/cash endpoint."""

    def test_returns_cash_balances(self, app_client):
        resp = app_client.get("/api/broker/cash")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2

    def test_cash_values_correct(self, app_client):
        resp = app_client.get("/api/broker/cash")
        items = resp.json()["items"]
        balances = {i["broker"]: i["balance"] for i in items}
        assert balances["ig"] == 25000.0
        assert balances["ibkr"] == 30000.0


class TestBrokerHealthAPI:
    """Tests for /api/broker/health endpoint."""

    def test_returns_broker_health(self, app_client):
        resp = app_client.get("/api/broker/health")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2

    def test_health_structure(self, app_client):
        resp = app_client.get("/api/broker/health")
        items = resp.json()["items"]
        for item in items:
            assert "broker" in item
            assert "accounts" in item
            assert "status" in item
            assert item["status"] == "registered"


class TestNavHistoryAPI:
    """Tests for /api/nav/history endpoint."""

    def test_returns_nav_history(self, app_client):
        resp = app_client.get("/api/nav/history")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2  # 2 fund-level snapshots

    def test_nav_ordered_desc(self, app_client):
        resp = app_client.get("/api/nav/history")
        items = resp.json()["items"]
        dates = [i["snapshot_date"] for i in items]
        assert dates == sorted(dates, reverse=True)


class TestRiskVerdictsAPI:
    """Tests for /api/risk/verdicts and /api/risk/summary endpoints."""

    def test_returns_verdicts(self, app_client):
        resp = app_client.get("/api/risk/verdicts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 6

    def test_filter_approved(self, app_client):
        resp = app_client.get("/api/risk/verdicts?approved=1")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3

    def test_filter_rejected(self, app_client):
        resp = app_client.get("/api/risk/verdicts?approved=0")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3

    def test_filter_by_ticker(self, app_client):
        resp = app_client.get("/api/risk/verdicts?ticker=TSLA")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["ticker"] == "TSLA"

    def test_risk_summary(self, app_client):
        resp = app_client.get("/api/risk/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6
        assert data["approved"] == 3
        assert data["rejected"] == 3


class TestReconciliationAPI:
    """Tests for /api/reconciliation/reports endpoint."""

    def test_returns_reports(self, app_client):
        resp = app_client.get("/api/reconciliation/reports")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1

    def test_report_structure(self, app_client):
        resp = app_client.get("/api/reconciliation/reports")
        items = resp.json()["items"]
        report = items[0]
        assert "status" in report
        assert "positions_checked" in report


# ─── Page & Fragment Tests ────────────────────────────────────────────────


class TestFundPage:
    """Tests for the /fund HTML page."""

    def test_fund_page_returns_html(self, app_client):
        resp = app_client.get("/fund")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_fund_page_has_panels(self, app_client):
        resp = app_client.get("/fund")
        html = resp.text
        assert "broker-health-panel" in html
        assert "ledger-positions-panel" in html
        assert "ledger-cash-panel" in html
        assert "risk-verdicts-panel" in html
        assert "nav-history-panel" in html
        assert "reconciliation-panel" in html


class TestNavigation:
    """Tests that navigation includes Fund link."""

    def test_fund_in_nav(self, app_client):
        resp = app_client.get("/overview")
        assert resp.status_code == 200
        assert 'href="/fund"' in resp.text
        assert ">Fund<" in resp.text


class TestHTMXFragments:
    """Tests for HTMX fragment endpoints."""

    def test_broker_health_fragment(self, app_client):
        resp = app_client.get("/fragments/broker-health")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Broker Health" in resp.text
        assert "IG" in resp.text.upper()

    def test_ledger_positions_fragment(self, app_client):
        resp = app_client.get("/fragments/ledger-positions")
        assert resp.status_code == 200
        assert "Unified Positions" in resp.text
        assert "SPY" in resp.text
        assert "FTSE100" in resp.text

    def test_ledger_positions_filter_broker(self, app_client):
        resp = app_client.get("/fragments/ledger-positions?broker=ig")
        assert resp.status_code == 200
        assert "FTSE100" in resp.text
        # SPY is IBKR-only, should not appear
        assert "SPY" not in resp.text

    def test_ledger_cash_fragment(self, app_client):
        resp = app_client.get("/fragments/ledger-cash")
        assert resp.status_code == 200
        assert "Cash Balances" in resp.text
        assert "Total Cash" in resp.text

    def test_risk_verdicts_fragment(self, app_client):
        resp = app_client.get("/fragments/risk-verdicts")
        assert resp.status_code == 200
        assert "Pre-Trade Risk Verdicts" in resp.text
        assert "PASS" in resp.text
        assert "REJECT" in resp.text

    def test_nav_history_fragment(self, app_client):
        resp = app_client.get("/fragments/nav-history")
        assert resp.status_code == 200
        assert "NAV History" in resp.text

    def test_reconciliation_reports_fragment(self, app_client):
        resp = app_client.get("/fragments/reconciliation-reports")
        assert resp.status_code == 200
        assert "Reconciliation Reports" in resp.text

    def test_empty_fragments_show_placeholder(self, db_path):
        """Fragments with no data should show placeholder text, not crash."""
        import data.trade_db as tdb
        import execution.ledger as ledger_mod

        original_db = tdb.DB_PATH
        original_ledger = ledger_mod.DB_PATH
        tdb.DB_PATH = db_path
        ledger_mod.DB_PATH = db_path

        from app.api.server import create_app
        from starlette.testclient import TestClient

        test_app = create_app()
        client = TestClient(test_app)

        # All fragments should return 200 even with empty data
        for frag in [
            "/fragments/broker-health",
            "/fragments/ledger-positions",
            "/fragments/ledger-cash",
            "/fragments/risk-verdicts",
            "/fragments/nav-history",
            "/fragments/reconciliation-reports",
        ]:
            resp = client.get(frag)
            assert resp.status_code == 200, f"{frag} failed with {resp.status_code}"

        tdb.DB_PATH = original_db
        ledger_mod.DB_PATH = original_ledger


# ─── Existing endpoint regression ────────────────────────────────────────


class TestExistingEndpoints:
    """Verify existing endpoints still work after A-007 changes."""

    def test_health(self, app_client):
        resp = app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_overview_page(self, app_client):
        resp = app_client.get("/overview")
        assert resp.status_code == 200

    def test_trading_page(self, app_client):
        resp = app_client.get("/trading")
        assert resp.status_code == 200

    def test_research_page(self, app_client):
        resp = app_client.get("/research")
        assert resp.status_code == 200

    def test_incidents_page(self, app_client):
        resp = app_client.get("/incidents")
        assert resp.status_code == 200

    def test_settings_page(self, app_client):
        resp = app_client.get("/settings")
        assert resp.status_code == 200
