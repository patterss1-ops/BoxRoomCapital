"""Tests for execution.order_lifecycle helpers."""

from __future__ import annotations

from execution.oms import OrderState
from execution.order_lifecycle import can_transition, is_terminal_state


def test_terminal_state_helper():
    assert is_terminal_state(OrderState.FILLED) is True
    assert is_terminal_state(OrderState.SUBMITTED) is False


def test_transition_helper():
    assert can_transition(OrderState.PENDING, OrderState.SUBMITTED) is True
    assert can_transition(OrderState.PENDING, OrderState.FILLED) is False
    assert can_transition(OrderState.PARTIAL, OrderState.FILLED) is True
