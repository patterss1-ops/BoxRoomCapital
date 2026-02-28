"""DB tests for multi-broker ledger extension (A-005)."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import trade_db


def _init_test_db(tmp_path):
    db_path = tmp_path / "ledger.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def test_unified_ledger_snapshot_reads_accounts_positions_cash_and_nav(tmp_path):
    db_path = _init_test_db(tmp_path)
    now = datetime.now().isoformat()

    trade_db.upsert_broker_account(
        broker="ig",
        account_id="ACC-IG-1",
        account_type="SPREADBET",
        account_label="IG Main",
        currency="GBP",
        status="active",
        db_path=db_path,
    )
    trade_db.replace_broker_positions(
        broker="ig",
        account_id="ACC-IG-1",
        positions=[
            {
                "position_id": "D-100",
                "ticker": "SPY",
                "instrument_type": "option",
                "direction": "short",
                "qty": 1,
                "avg_price": 24.0,
                "market_price": 20.0,
                "unrealised_pnl": 4.0,
                "as_of": now,
            }
        ],
        db_path=db_path,
    )
    trade_db.upsert_broker_cash_balance(
        broker="ig",
        account_id="ACC-IG-1",
        currency="GBP",
        balance=5000.0,
        equity=5200.0,
        available=4800.0,
        margin_used=400.0,
        as_of=now,
        db_path=db_path,
    )
    trade_db.insert_nav_snapshot(
        timestamp=now,
        sleeve="options_income",
        broker="ig",
        account_id="ACC-IG-1",
        nav=5200.0,
        cash=5000.0,
        gross_exposure=1200.0,
        net_exposure=800.0,
        source="unit_test",
        db_path=db_path,
    )
    trade_db.upsert_option_position(
        spread_id="SPY:abc123",
        ticker="SPY",
        strategy="IBS Credit Spreads",
        trade_type="put_spread",
        short_deal_id="D-100",
        long_deal_id="D-101",
        short_strike=5200.0,
        long_strike=5150.0,
        short_epic="OP.D.SPXWEEKLY.5200P.IP",
        long_epic="OP.D.SPXWEEKLY.5150P.IP",
        spread_width=50.0,
        premium_collected=6.0,
        max_loss=44.0,
        size=1.0,
        db_path=db_path,
    )

    snapshot = trade_db.get_unified_ledger_snapshot(nav_limit=10, db_path=db_path)

    assert snapshot["summary"]["accounts"] == 1
    assert snapshot["summary"]["positions"] == 1
    assert snapshot["summary"]["cash_rows"] == 1
    assert snapshot["summary"]["total_cash"] == 5000.0
    assert snapshot["summary"]["total_equity"] == 5200.0
    assert len(snapshot["nav_snapshots"]) == 1

    report = trade_db.get_ledger_reconcile_report(stale_after_minutes=30, db_path=db_path)
    assert report["ok"] is True
    assert report["ig_count_mismatch"] is False
    assert report["orphan_position_count"] == 0
    assert report["stale_position_count"] == 0


def test_ledger_reconcile_flags_mismatch_orphans_and_stale_rows(tmp_path):
    db_path = _init_test_db(tmp_path)
    old_stamp = (datetime.now() - timedelta(minutes=120)).isoformat()
    now = datetime.now().isoformat()

    trade_db.upsert_option_position(
        spread_id="SPY:mismatch",
        ticker="SPY",
        strategy="IBS Credit Spreads",
        trade_type="put_spread",
        short_deal_id="D-200",
        long_deal_id="D-201",
        short_strike=5200.0,
        long_strike=5150.0,
        short_epic="OP.D.SPXWEEKLY.5200P.IP",
        long_epic="OP.D.SPXWEEKLY.5150P.IP",
        spread_width=50.0,
        premium_collected=6.0,
        max_loss=44.0,
        size=1.0,
        db_path=db_path,
    )

    report_mismatch = trade_db.get_ledger_reconcile_report(stale_after_minutes=30, db_path=db_path)
    assert report_mismatch["ok"] is False
    assert report_mismatch["ig_count_mismatch"] is True
    assert any("count differs" in s.lower() for s in report_mismatch["suggestions"])

    trade_db.replace_broker_positions(
        broker="ig",
        account_id="ACC-MISSING",
        positions=[
            {
                "position_id": "D-200",
                "ticker": "SPY",
                "qty": 1,
                "as_of": old_stamp,
            }
        ],
        db_path=db_path,
    )
    report_orphan = trade_db.get_ledger_reconcile_report(stale_after_minutes=30, db_path=db_path)
    assert report_orphan["orphan_position_count"] == 1
    assert report_orphan["stale_position_count"] == 1
    assert report_orphan["ok"] is False

    trade_db.upsert_broker_account(
        broker="ig",
        account_id="ACC-MISSING",
        account_type="SPREADBET",
        currency="GBP",
        status="active",
        db_path=db_path,
    )
    trade_db.replace_broker_positions(
        broker="ig",
        account_id="ACC-MISSING",
        positions=[
            {
                "position_id": "D-200",
                "ticker": "SPY",
                "qty": 1,
                "as_of": now,
            }
        ],
        db_path=db_path,
    )
    report_clean = trade_db.get_ledger_reconcile_report(stale_after_minutes=30, db_path=db_path)
    assert report_clean["ok"] is True
    assert report_clean["orphan_position_count"] == 0
    assert report_clean["stale_position_count"] == 0
