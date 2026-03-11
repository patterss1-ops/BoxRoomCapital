"""Tests for intelligence.advisory_holdings — portfolio tracking across tax wrappers."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from data.trade_db import get_conn


@pytest.fixture()
def db(tmp_path):
    """Return a fresh SQLite DB path with holdings tables initialised."""
    db_path = str(tmp_path / "test_holdings.db")
    # Clear the module-level cache so schema is re-created
    from intelligence.advisory_holdings import _tables_ensured
    _tables_ensured.discard(db_path)
    from intelligence.advisory_holdings import _ensure_tables
    _ensure_tables(db_path)
    return db_path


# ── 1. Add holding ────────────────────────────────────────────────────────

def test_add_holding(db):
    from intelligence.advisory_holdings import add_holding, get_holdings

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        hid = add_holding(db, "ISA", "VWRL.L", 100.0, 80.50, name="Vanguard FTSE All-World")

    assert isinstance(hid, str)
    assert len(hid) == 8

    holdings = get_holdings(db)
    assert len(holdings) == 1
    assert holdings[0]["ticker"] == "VWRL.L"
    assert holdings[0]["wrapper"] == "ISA"
    assert holdings[0]["quantity"] == 100.0
    assert holdings[0]["avg_cost"] == 80.50


# ── 2. Add holding with duplicate (same wrapper+ticker) ──────────────────

def test_add_duplicate_holding(db):
    from intelligence.advisory_holdings import add_holding, get_holdings

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        add_holding(db, "ISA", "VWRL.L", 50.0, 80.00)
        add_holding(db, "ISA", "VWRL.L", 25.0, 82.00)

    # Same wrapper+ticker should be merged via cost averaging
    holdings = get_holdings(db)
    assert len(holdings) == 1
    h = holdings[0]
    assert h["quantity"] == 75.0  # 50 + 25
    # Weighted avg: (50*80 + 25*82) / 75 = 80.6667
    assert round(h["avg_cost"], 2) == pytest.approx(80.67, abs=0.01)


# ── 3. Close holding ─────────────────────────────────────────────────────

def test_close_holding(db):
    from intelligence.advisory_holdings import add_holding, close_holding, get_holdings

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        hid = add_holding(db, "SIPP", "VUSA.L", 200.0, 50.00)

    result = close_holding(hid, 55.00, db_path=db)
    assert result["realized_pnl"] == 1000.0  # (55-50) * 200
    assert result["realized_pnl_pct"] == 10.0
    assert result["ticker"] == "VUSA.L"

    # Open holdings should be empty now
    open_holdings = get_holdings(db, status="open")
    assert len(open_holdings) == 0

    # Closed holdings should have it
    closed = get_holdings(db, status="closed")
    assert len(closed) == 1


# ── 4. Get holdings (all + filtered by wrapper) ──────────────────────────

def test_get_holdings_filtered(db):
    from intelligence.advisory_holdings import add_holding, get_holdings

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        add_holding(db, "ISA", "VWRL.L", 100.0, 80.00)
        add_holding(db, "SIPP", "VUSA.L", 50.0, 50.00)
        add_holding(db, "GIA", "AAPL", 10.0, 150.00)

    all_holdings = get_holdings(db)
    assert len(all_holdings) == 3

    isa_only = get_holdings(db, wrapper="ISA")
    assert len(isa_only) == 1
    assert isa_only[0]["ticker"] == "VWRL.L"

    sipp_only = get_holdings(db, wrapper="SIPP")
    assert len(sipp_only) == 1
    assert sipp_only[0]["ticker"] == "VUSA.L"


# ── 5. Fetch live prices (mock yfinance) ─────────────────────────────────

def test_fetch_live_prices(db):
    from intelligence.advisory_holdings import fetch_live_prices

    import pandas as pd
    mock_data = pd.DataFrame(
        {"Close": [85.50]},
        index=pd.DatetimeIndex(["2026-03-10"]),
    )

    with patch("intelligence.advisory_holdings._YF_AVAILABLE", True):
        with patch("intelligence.advisory_holdings.yf") as mock_yf:
            mock_yf.download.return_value = mock_data
            prices = fetch_live_prices(["VWRL.L"], db_path=db)

    assert "VWRL.L" in prices
    assert prices["VWRL.L"] == pytest.approx(85.50)


# ── 6. Price cache (verify caching) ──────────────────────────────────────

def test_price_cache_hit(db):
    from intelligence.advisory_holdings import fetch_live_prices

    # Seed cache directly
    conn = get_conn(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO advisory_price_cache (ticker, price, fetched_at) VALUES (?, ?, ?)",
        ("VWRL.L", 84.00, now),
    )
    conn.commit()

    # Should return cached price without calling yfinance
    with patch("intelligence.advisory_holdings._YF_AVAILABLE", True):
        with patch("intelligence.advisory_holdings.yf") as mock_yf:
            prices = fetch_live_prices(["VWRL.L"], db_path=db)
            # yfinance download should NOT be called
            mock_yf.download.assert_not_called()

    assert prices["VWRL.L"] == 84.00


# ── 7. Portfolio snapshot calculation ─────────────────────────────────────

def test_portfolio_snapshot(db):
    from intelligence.advisory_holdings import add_holding, calculate_portfolio_snapshot

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        add_holding(db, "ISA", "VWRL.L", 100.0, 80.00, name="Vanguard FTSE All-World")

    # Mock live price
    with patch("intelligence.advisory_holdings.fetch_live_prices", return_value={"VWRL.L": 85.00}):
        snap = calculate_portfolio_snapshot(db)

    assert snap["total_cost"] == 8000.00
    assert snap["total_value"] == 8500.00
    assert snap["total_pnl"] == 500.00
    assert snap["total_pnl_pct"] == pytest.approx(6.25)
    assert "ISA" in snap["wrappers"]
    assert len(snap["wrappers"]["ISA"]["holdings"]) == 1


# ── 8. Wrapper summary with allowances ───────────────────────────────────

def test_wrapper_summary(db):
    from intelligence.advisory_holdings import get_wrapper_summary, update_wrapper_allowance, _current_tax_year

    tax_year = _current_tax_year()
    update_wrapper_allowance(db, tax_year, "ISA", 5000.0)

    summary = get_wrapper_summary(db)
    assert "ISA" in summary
    assert summary["ISA"]["used"] == 5000.0
    assert summary["ISA"]["limit"] == 20_000
    assert summary["ISA"]["remaining"] == 15_000.0

    # GIA has no statutory limit
    assert summary["GIA"]["limit"] is None
    assert summary["GIA"]["remaining"] is None


# ── 9. Update wrapper allowance ──────────────────────────────────────────

def test_update_wrapper_allowance(db):
    from intelligence.advisory_holdings import update_wrapper_allowance, get_wrapper_summary, _current_tax_year

    tax_year = _current_tax_year()
    update_wrapper_allowance(db, tax_year, "SIPP", 10000.0)

    summary = get_wrapper_summary(db)
    assert summary["SIPP"]["used"] == 10000.0
    assert summary["SIPP"]["remaining"] == 50_000.0

    # Update again — should upsert
    update_wrapper_allowance(db, tax_year, "SIPP", 25000.0)
    summary = get_wrapper_summary(db)
    assert summary["SIPP"]["used"] == 25000.0

    # Invalid wrapper should raise
    with pytest.raises(ValueError, match="Invalid wrapper"):
        update_wrapper_allowance(db, tax_year, "INVALID", 100.0)


# ── 10. Format telegram output ───────────────────────────────────────────

def test_format_holdings_telegram(db):
    from intelligence.advisory_holdings import add_holding, format_holdings_telegram

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        add_holding(db, "ISA", "VWRL.L", 100.0, 80.00, name="Vanguard FTSE All-World")

    with patch("intelligence.advisory_holdings.fetch_live_prices", return_value={"VWRL.L": 85.00}):
        output = format_holdings_telegram(db)

    assert "ADVISORY PORTFOLIO" in output
    assert "ISA" in output
    assert "Vanguard FTSE All-World" in output

    # Empty portfolio
    from intelligence.advisory_holdings import _tables_ensured
    empty_db = str(db + ".empty")
    _tables_ensured.discard(empty_db)
    empty_output = format_holdings_telegram(empty_db)
    assert "No advisory holdings" in empty_output


# ── 11. Format performance telegram ──────────────────────────────────────

def test_format_performance_telegram(db):
    from intelligence.advisory_holdings import add_holding, format_performance_telegram

    with patch("intelligence.advisory_holdings._validate_ticker", return_value=True):
        add_holding(
            db, "ISA", "VWRL.L", 100.0, 80.00,
            name="Vanguard FTSE All-World",
            benchmark_ticker="^FTSE",
            purchase_date="2025-01-01",
        )

    perf_result = {
        "ticker": "VWRL.L",
        "benchmark": "^FTSE",
        "since": "2025-01-01",
        "ticker_return": 12.5,
        "benchmark_return": 8.0,
        "relative_return": 4.5,
    }

    with patch("intelligence.advisory_holdings.calculate_performance_vs_benchmark", return_value=perf_result):
        output = format_performance_telegram(db)

    assert "PERFORMANCE REPORT" in output
    assert "Vanguard FTSE All-World" in output


# ── 12. Performance vs benchmark (mock yfinance) ─────────────────────────

def test_performance_vs_benchmark(db):
    from intelligence.advisory_holdings import calculate_performance_vs_benchmark

    import pandas as pd

    mock_hist = pd.DataFrame(
        {"Close": [100.0, 110.0]},
        index=pd.DatetimeIndex(["2025-01-01", "2025-06-01"]),
    )

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = mock_hist

    with patch("intelligence.advisory_holdings._YF_AVAILABLE", True):
        with patch("intelligence.advisory_holdings.yf") as mock_yf:
            mock_yf.Ticker.return_value = mock_ticker
            result = calculate_performance_vs_benchmark(db, "VWRL.L", "^FTSE", "2025-01-01")

    assert result["ticker_return"] == 10.0
    assert result["benchmark_return"] == 10.0
    assert result["relative_return"] == 0.0
