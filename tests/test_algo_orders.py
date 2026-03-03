"""Tests for TWAP / VWAP execution algorithms (M-001)."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from execution.algo_orders import (
    AlgoExecutionEngine,
    AlgoOrderConfig,
    AlgoOrderState,
    AlgoType,
    OrderSlice,
    SliceStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> AlgoOrderConfig:
    defaults = dict(
        algo_type=AlgoType.TWAP,
        ticker="AAPL",
        side="BUY",
        total_qty=1000.0,
        duration_seconds=600,
        num_slices=10,
    )
    defaults.update(overrides)
    return AlgoOrderConfig(**defaults)


def _engine_with_order(**overrides) -> tuple[AlgoExecutionEngine, AlgoOrderState]:
    engine = AlgoExecutionEngine()
    cfg = _make_config(**overrides)
    state = engine.create_order(cfg)
    return engine, state


# ---------------------------------------------------------------------------
# 1. TWAP creates equal slices
# ---------------------------------------------------------------------------

def test_twap_equal_slices():
    engine, state = _engine_with_order(
        algo_type=AlgoType.TWAP, total_qty=1000, num_slices=5,
    )
    quantities = [s.target_qty for s in state.slices]
    assert len(quantities) == 5
    assert all(math.isclose(q, 200.0) for q in quantities)


# ---------------------------------------------------------------------------
# 2. VWAP creates front-loaded slices (30 / 50 / 20)
# ---------------------------------------------------------------------------

def test_vwap_distribution():
    engine, state = _engine_with_order(
        algo_type=AlgoType.VWAP, total_qty=3000, num_slices=9,
    )
    # 9 slices -> 3 per bucket
    first_third = sum(s.target_qty for s in state.slices[:3])
    mid_third = sum(s.target_qty for s in state.slices[3:6])
    last_third = sum(s.target_qty for s in state.slices[6:])
    assert math.isclose(first_third, 900.0, rel_tol=1e-9)   # 30 %
    assert math.isclose(mid_third, 1500.0, rel_tol=1e-9)     # 50 %
    assert math.isclose(last_third, 600.0, rel_tol=1e-9)     # 20 %


# ---------------------------------------------------------------------------
# 3. Fill a slice and verify state update
# ---------------------------------------------------------------------------

def test_fill_slice_updates_state():
    engine, state = _engine_with_order(num_slices=4, total_qty=400)
    sl = engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    assert sl.status == SliceStatus.FILLED
    assert sl.filled_qty == 100.0
    assert sl.avg_price == 150.0
    refreshed = engine.get_order(state.order_id)
    assert refreshed is not None
    assert refreshed.total_filled == 100.0


# ---------------------------------------------------------------------------
# 4. Partial fill tracking
# ---------------------------------------------------------------------------

def test_partial_fill():
    engine, state = _engine_with_order(num_slices=4, total_qty=400)
    sl = engine.fill_slice(state.order_id, 0, 50.0, 150.0)
    assert sl.status == SliceStatus.PARTIALLY_FILLED
    assert sl.filled_qty == 50.0


# ---------------------------------------------------------------------------
# 5. Cancel order cancels remaining slices
# ---------------------------------------------------------------------------

def test_cancel_order():
    engine, state = _engine_with_order(num_slices=4, total_qty=400)
    engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    engine.cancel_order(state.order_id)
    statuses = [s.status for s in state.slices]
    assert statuses[0] == SliceStatus.FILLED
    assert all(s == SliceStatus.CANCELLED for s in statuses[1:])
    assert state.status == "CANCELLED"


# ---------------------------------------------------------------------------
# 6. Get next slice returns first PENDING
# ---------------------------------------------------------------------------

def test_get_next_slice():
    engine, state = _engine_with_order(num_slices=4, total_qty=400)
    engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    nxt = engine.get_next_slice(state.order_id)
    assert nxt is not None
    assert nxt.slice_index == 1
    assert nxt.status == SliceStatus.PENDING


# ---------------------------------------------------------------------------
# 7. Order completion calculation
# ---------------------------------------------------------------------------

def test_completion_calculation():
    engine, state = _engine_with_order(num_slices=4, total_qty=400)
    engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    engine.fill_slice(state.order_id, 1, 100.0, 155.0)
    updated = engine.update_completion(state.order_id)
    assert math.isclose(updated.completion_pct, 50.0)
    assert math.isclose(updated.total_filled, 200.0)


# ---------------------------------------------------------------------------
# 8. 100 % filled marks order COMPLETED
# ---------------------------------------------------------------------------

def test_fully_filled_marks_completed():
    engine, state = _engine_with_order(num_slices=4, total_qty=400)
    for i in range(4):
        engine.fill_slice(state.order_id, i, 100.0, 150.0)
    assert state.status == "COMPLETED"
    assert math.isclose(state.completion_pct, 100.0)


# ---------------------------------------------------------------------------
# 9. Price limit validation (slice price check)
# ---------------------------------------------------------------------------

def test_price_limit_buy_breach():
    engine, state = _engine_with_order(
        num_slices=2, total_qty=200, price_limit=100.0, side="BUY",
    )
    with pytest.raises(ValueError, match="exceeds buy limit"):
        engine.fill_slice(state.order_id, 0, 100.0, 110.0)
    assert state.slices[0].status == SliceStatus.FAILED


def test_price_limit_sell_breach():
    engine, state = _engine_with_order(
        num_slices=2, total_qty=200, price_limit=100.0, side="SELL",
    )
    with pytest.raises(ValueError, match="below sell limit"):
        engine.fill_slice(state.order_id, 0, 100.0, 90.0)
    assert state.slices[0].status == SliceStatus.FAILED


# ---------------------------------------------------------------------------
# 10. Urgency affects nothing structurally (just stored)
# ---------------------------------------------------------------------------

def test_urgency_stored():
    engine, state = _engine_with_order(urgency=0.9, num_slices=3, total_qty=300)
    assert state.config.urgency == 0.9
    quantities = [s.target_qty for s in state.slices]
    assert all(math.isclose(q, 100.0) for q in quantities)


# ---------------------------------------------------------------------------
# 11. Multiple orders tracked independently
# ---------------------------------------------------------------------------

def test_multiple_orders():
    engine = AlgoExecutionEngine()
    s1 = engine.create_order(_make_config(ticker="AAPL", num_slices=2, total_qty=200))
    s2 = engine.create_order(_make_config(ticker="GOOG", num_slices=3, total_qty=300))
    assert len(engine.list_orders()) == 2
    assert engine.get_order(s1.order_id) is not None
    assert engine.get_order(s2.order_id) is not None
    assert s1.order_id != s2.order_id


# ---------------------------------------------------------------------------
# 12. Fill all slices sequentially
# ---------------------------------------------------------------------------

def test_fill_all_sequentially():
    engine, state = _engine_with_order(num_slices=5, total_qty=500)
    for i in range(5):
        engine.fill_slice(state.order_id, i, 100.0, 150.0 + i)
    assert state.status == "COMPLETED"
    assert math.isclose(state.total_filled, 500.0)
    # Weighted average price: (150*100 + 151*100 + 152*100 + 153*100 + 154*100) / 500
    expected_avg = (150 + 151 + 152 + 153 + 154) / 5
    assert math.isclose(state.avg_fill_price, expected_avg, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 13. Cancel already completed order is no-op
# ---------------------------------------------------------------------------

def test_cancel_completed_is_noop():
    engine, state = _engine_with_order(num_slices=2, total_qty=200)
    engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    engine.fill_slice(state.order_id, 1, 100.0, 150.0)
    assert state.status == "COMPLETED"
    result = engine.cancel_order(state.order_id)
    assert result.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 14. Get nonexistent order returns None
# ---------------------------------------------------------------------------

def test_get_nonexistent_order():
    engine = AlgoExecutionEngine()
    assert engine.get_order("does-not-exist") is None


# ---------------------------------------------------------------------------
# 15. Slice scheduling times are evenly spaced (TWAP)
# ---------------------------------------------------------------------------

def test_twap_even_scheduling():
    engine, state = _engine_with_order(
        algo_type=AlgoType.TWAP, num_slices=5, duration_seconds=500,
    )
    times = [datetime.fromisoformat(s.scheduled_at) for s in state.slices]
    deltas = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    assert all(math.isclose(d, 100.0, abs_tol=0.01) for d in deltas)


# ---------------------------------------------------------------------------
# 16. VWAP quantity distribution sums to total
# ---------------------------------------------------------------------------

def test_vwap_sums_to_total():
    for n in (3, 6, 9, 12, 15):
        engine, state = _engine_with_order(
            algo_type=AlgoType.VWAP, total_qty=1000, num_slices=n,
        )
        total = sum(s.target_qty for s in state.slices)
        assert math.isclose(total, 1000.0, rel_tol=1e-9), f"n={n}, total={total}"


# ---------------------------------------------------------------------------
# 17. Order status transitions
# ---------------------------------------------------------------------------

def test_status_transitions():
    engine, state = _engine_with_order(num_slices=3, total_qty=300)
    assert state.status == "ACTIVE"
    engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    assert state.status == "ACTIVE"
    engine.fill_slice(state.order_id, 1, 100.0, 150.0)
    assert state.status == "ACTIVE"
    engine.fill_slice(state.order_id, 2, 100.0, 150.0)
    assert state.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 18. Average fill price weighted correctly
# ---------------------------------------------------------------------------

def test_weighted_avg_price():
    engine, state = _engine_with_order(num_slices=2, total_qty=200)
    engine.fill_slice(state.order_id, 0, 100.0, 100.0)
    engine.fill_slice(state.order_id, 1, 100.0, 200.0)
    # Expected: (100*100 + 200*100) / 200 = 150
    assert math.isclose(state.avg_fill_price, 150.0)


# ---------------------------------------------------------------------------
# 19. Zero-qty slices handled
# ---------------------------------------------------------------------------

def test_zero_qty_order():
    engine, state = _engine_with_order(num_slices=3, total_qty=0)
    assert len(state.slices) == 3
    assert all(s.target_qty == 0 for s in state.slices)
    updated = engine.update_completion(state.order_id)
    assert updated.completion_pct == 0.0


# ---------------------------------------------------------------------------
# 20. Create order with 1 slice
# ---------------------------------------------------------------------------

def test_single_slice():
    engine, state = _engine_with_order(num_slices=1, total_qty=500)
    assert len(state.slices) == 1
    assert math.isclose(state.slices[0].target_qty, 500.0)
    engine.fill_slice(state.order_id, 0, 500.0, 120.0)
    assert state.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 21. Partial fill then complete fill of same slice
# ---------------------------------------------------------------------------

def test_partial_then_full_fill():
    engine, state = _engine_with_order(num_slices=2, total_qty=200)
    sl = engine.fill_slice(state.order_id, 0, 40.0, 100.0)
    assert sl.status == SliceStatus.PARTIALLY_FILLED
    sl = engine.fill_slice(state.order_id, 0, 60.0, 110.0)
    assert sl.status == SliceStatus.FILLED
    # Weighted avg: (100*40 + 110*60) / 100 = 106
    assert math.isclose(sl.avg_price, 106.0, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 22. Price limit within bounds succeeds
# ---------------------------------------------------------------------------

def test_price_limit_within_bounds():
    engine, state = _engine_with_order(
        num_slices=2, total_qty=200, price_limit=100.0, side="BUY",
    )
    sl = engine.fill_slice(state.order_id, 0, 100.0, 95.0)
    assert sl.status == SliceStatus.FILLED


# ---------------------------------------------------------------------------
# 23. VWAP with 2 slices
# ---------------------------------------------------------------------------

def test_vwap_two_slices():
    engine, state = _engine_with_order(
        algo_type=AlgoType.VWAP, total_qty=1000, num_slices=2,
    )
    assert len(state.slices) == 2
    total = sum(s.target_qty for s in state.slices)
    assert math.isclose(total, 1000.0, rel_tol=1e-9)
    # First slice should be larger (front-loaded)
    assert state.slices[0].target_qty > state.slices[1].target_qty


# ---------------------------------------------------------------------------
# 24. get_next_slice returns None when all filled
# ---------------------------------------------------------------------------

def test_get_next_slice_none_when_all_filled():
    engine, state = _engine_with_order(num_slices=2, total_qty=200)
    engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    engine.fill_slice(state.order_id, 1, 100.0, 150.0)
    assert engine.get_next_slice(state.order_id) is None


# ---------------------------------------------------------------------------
# 25. Slice executed_at is set on fill
# ---------------------------------------------------------------------------

def test_executed_at_set_on_fill():
    engine, state = _engine_with_order(num_slices=2, total_qty=200)
    sl = engine.fill_slice(state.order_id, 0, 100.0, 150.0)
    assert sl.executed_at != ""
    dt = datetime.fromisoformat(sl.executed_at)
    assert dt.tzinfo is not None
