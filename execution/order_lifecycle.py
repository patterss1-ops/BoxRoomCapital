"""Order lifecycle transition helpers for the OMS."""

from __future__ import annotations

from execution.oms import OrderState, TERMINAL_STATES, VALID_TRANSITIONS


def is_terminal_state(state: OrderState) -> bool:
    """Return True when no further transitions are allowed."""
    return state in TERMINAL_STATES


def can_transition(from_state: OrderState, to_state: OrderState) -> bool:
    """Return True when ``to_state`` is a valid transition from ``from_state``."""
    allowed = VALID_TRANSITIONS.get(from_state, frozenset())
    return to_state in allowed
