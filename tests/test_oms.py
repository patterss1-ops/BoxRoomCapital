"""Tests for I-004 Order Management System."""

from __future__ import annotations

import time

import pytest

from execution.oms import (
    Order,
    OrderManager,
    OrderState,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
)


class TestOrderDataClass:
    def test_order_auto_id(self):
        o = Order(order_id="", ticker="AAPL", direction="BUY", size=10)
        assert o.order_id != ""

    def test_order_to_dict(self):
        o = Order(order_id="abc", ticker="AAPL", direction="BUY", size=10)
        d = o.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["state"] == "pending"

    def test_fill_pct(self):
        o = Order(order_id="x", ticker="T", direction="BUY", size=100, filled_size=50)
        assert o.fill_pct == 50.0

    def test_is_terminal(self):
        o = Order(order_id="x", ticker="T", direction="BUY", size=10, state=OrderState.FILLED)
        assert o.is_terminal is True


class TestOrderManager:
    def test_create_order(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 100, strategy="momentum")
        assert order.state == OrderState.PENDING
        assert order.ticker == "AAPL"
        assert order.order_id in mgr.orders

    def test_full_lifecycle_pending_to_filled(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 100)
        mgr.submit(order.order_id, broker_ref="BR123")
        assert order.state == OrderState.SUBMITTED
        assert order.broker_ref == "BR123"

        mgr.fill(order.order_id, 100, 150.0)
        assert order.state == OrderState.FILLED
        assert order.filled_price == 150.0
        assert order.is_terminal

    def test_partial_fill(self):
        mgr = OrderManager()
        order = mgr.create_order("MSFT", "BUY", 100)
        mgr.submit(order.order_id)
        mgr.fill(order.order_id, 50, 300.0)
        assert order.state == OrderState.PARTIAL
        assert order.filled_size == 50
        assert not order.is_terminal

        mgr.fill(order.order_id, 100, 301.0)
        assert order.state == OrderState.FILLED

    def test_cancel_submitted(self):
        mgr = OrderManager()
        order = mgr.create_order("GOOG", "SELL", 50)
        mgr.submit(order.order_id)
        mgr.cancel(order.order_id, reason="user request")
        assert order.state == OrderState.CANCELLED
        assert order.error_message == "user request"

    def test_reject_pending(self):
        mgr = OrderManager()
        order = mgr.create_order("TSLA", "BUY", 200)
        mgr.reject(order.order_id, reason="insufficient margin")
        assert order.state == OrderState.REJECTED

    def test_invalid_transition_raises(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 10)
        with pytest.raises(ValueError, match="Invalid transition"):
            mgr.fill(order.order_id, 10, 150.0)  # Can't fill from PENDING

    def test_terminal_state_blocks_transition(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 10)
        mgr.submit(order.order_id)
        mgr.fill(order.order_id, 10, 150.0)
        with pytest.raises(ValueError, match="terminal"):
            mgr.cancel(order.order_id)

    def test_timeout_enforcement(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 10, timeout_secs=0.01)
        mgr.submit(order.order_id)
        time.sleep(0.02)
        timed_out = mgr.check_timeouts()
        assert len(timed_out) == 1
        assert order.state == OrderState.TIMEOUT

    def test_no_timeout_when_within_limit(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 10, timeout_secs=999)
        mgr.submit(order.order_id)
        timed_out = mgr.check_timeouts()
        assert len(timed_out) == 0
        assert order.state == OrderState.SUBMITTED

    def test_retry_tracking(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 10, max_retries=3)
        assert mgr.can_retry(order.order_id) is True
        mgr.record_retry(order.order_id)
        mgr.record_retry(order.order_id)
        mgr.record_retry(order.order_id)
        assert mgr.can_retry(order.order_id) is False

    def test_retry_backoff_is_exponential_and_capped(self):
        mgr = OrderManager()
        order = mgr.create_order("AAPL", "BUY", 10, max_retries=5)
        order.retry_backoff_base_secs = 2.0
        assert mgr.next_retry_delay_secs(order.order_id) == 2.0
        mgr.record_retry(order.order_id)
        assert mgr.next_retry_delay_secs(order.order_id) == 4.0
        mgr.record_retry(order.order_id)
        assert mgr.next_retry_delay_secs(order.order_id) == 8.0
        # Cap at max_backoff_secs
        order.retry_count = 10
        assert mgr.next_retry_delay_secs(order.order_id, max_backoff_secs=30.0) == 30.0

    def test_get_active_orders(self):
        mgr = OrderManager()
        o1 = mgr.create_order("AAPL", "BUY", 10)
        o2 = mgr.create_order("MSFT", "SELL", 20)
        mgr.submit(o1.order_id)
        mgr.fill(o1.order_id, 10, 150.0)
        active = mgr.get_active_orders()
        assert len(active) == 1
        assert active[0].order_id == o2.order_id

    def test_get_nonexistent_order(self):
        mgr = OrderManager()
        assert mgr.get_order("nonexistent") is None

    def test_get_nonexistent_raises_on_submit(self):
        mgr = OrderManager()
        with pytest.raises(KeyError):
            mgr.submit("nonexistent")
