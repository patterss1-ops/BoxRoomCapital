"""Order Management System — order lifecycle state machine.

I-004: Tracks orders through their lifecycle: PENDING → SUBMITTED → FILLED,
with support for PARTIAL fills, CANCELLED, REJECTED, and TIMEOUT states.
Provides timeout enforcement and retry tracking.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OrderState(str, Enum):
    """Order lifecycle states."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


# Terminal states — no further transitions allowed
TERMINAL_STATES = frozenset({
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.TIMEOUT,
})

# Valid state transitions
VALID_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING: frozenset({OrderState.SUBMITTED, OrderState.CANCELLED, OrderState.REJECTED}),
    OrderState.SUBMITTED: frozenset({OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.TIMEOUT}),
    OrderState.PARTIAL: frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.TIMEOUT}),
}


@dataclass
class Order:
    """An order tracked through its lifecycle."""

    order_id: str
    ticker: str
    direction: str  # BUY | SELL
    size: float
    strategy: str = ""
    state: OrderState = OrderState.PENDING
    filled_size: float = 0.0
    filled_price: Optional[float] = None
    broker_ref: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    retry_backoff_base_secs: float = 1.0
    timeout_secs: float = 60.0
    created_at: str = ""
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None

    def __post_init__(self):
        if not self.order_id:
            self.order_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def fill_pct(self) -> float:
        if self.size <= 0:
            return 0.0
        return (self.filled_size / self.size) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "direction": self.direction,
            "size": self.size,
            "state": self.state.value,
            "filled_size": self.filled_size,
            "filled_price": self.filled_price,
            "broker_ref": self.broker_ref,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
        }


class OrderManager:
    """Manages order lifecycle transitions and timeout enforcement."""

    def __init__(self):
        self._orders: dict[str, Order] = {}

    @property
    def orders(self) -> dict[str, Order]:
        return dict(self._orders)

    def create_order(
        self,
        ticker: str,
        direction: str,
        size: float,
        strategy: str = "",
        timeout_secs: float = 60.0,
        max_retries: int = 3,
    ) -> Order:
        """Create a new order in PENDING state."""
        order = Order(
            order_id=str(uuid.uuid4())[:8],
            ticker=ticker,
            direction=direction,
            size=size,
            strategy=strategy,
            timeout_secs=timeout_secs,
            max_retries=max_retries,
        )
        self._orders[order.order_id] = order
        return order

    def submit(self, order_id: str, broker_ref: Optional[str] = None) -> Order:
        """Transition order to SUBMITTED state."""
        order = self._get_order(order_id)
        self._transition(order, OrderState.SUBMITTED)
        order.submitted_at = datetime.now(timezone.utc).isoformat()
        if broker_ref:
            order.broker_ref = broker_ref
        return order

    def fill(self, order_id: str, filled_size: float, filled_price: float) -> Order:
        """Record a fill (full or partial)."""
        order = self._get_order(order_id)
        order.filled_size = min(filled_size, order.size)
        order.filled_price = filled_price

        if order.filled_size >= order.size:
            self._transition(order, OrderState.FILLED)
            order.completed_at = datetime.now(timezone.utc).isoformat()
        else:
            self._transition(order, OrderState.PARTIAL)
        return order

    def cancel(self, order_id: str, reason: str = "") -> Order:
        """Cancel an order."""
        order = self._get_order(order_id)
        self._transition(order, OrderState.CANCELLED)
        order.error_message = reason or "cancelled"
        order.completed_at = datetime.now(timezone.utc).isoformat()
        return order

    def reject(self, order_id: str, reason: str) -> Order:
        """Reject an order."""
        order = self._get_order(order_id)
        self._transition(order, OrderState.REJECTED)
        order.error_message = reason
        order.completed_at = datetime.now(timezone.utc).isoformat()
        return order

    def check_timeouts(self) -> list[Order]:
        """Check all submitted/partial orders for timeout. Returns timed-out orders."""
        now = datetime.now(timezone.utc)
        timed_out = []

        for order in self._orders.values():
            if order.state not in (OrderState.SUBMITTED, OrderState.PARTIAL):
                continue
            if not order.submitted_at:
                continue

            submitted = datetime.fromisoformat(order.submitted_at)
            if submitted.tzinfo is None:
                submitted = submitted.replace(tzinfo=timezone.utc)

            if (now - submitted).total_seconds() > order.timeout_secs:
                self._transition(order, OrderState.TIMEOUT)
                order.error_message = f"timeout after {order.timeout_secs}s"
                order.completed_at = now.isoformat()
                timed_out.append(order)

        return timed_out

    def can_retry(self, order_id: str) -> bool:
        """Check if an order can be retried."""
        order = self._get_order(order_id)
        return order.retry_count < order.max_retries

    def record_retry(self, order_id: str) -> Order:
        """Record a retry attempt."""
        order = self._get_order(order_id)
        order.retry_count += 1
        return order

    def next_retry_delay_secs(self, order_id: str, max_backoff_secs: float = 300.0) -> float:
        """Compute exponential backoff delay for the next retry attempt."""
        order = self._get_order(order_id)
        base = max(0.1, float(order.retry_backoff_base_secs or 1.0))
        exp = max(0, int(order.retry_count))
        return min(float(max_backoff_secs), base * (2 ** exp))

    def get_active_orders(self) -> list[Order]:
        """Get all non-terminal orders."""
        return [o for o in self._orders.values() if not o.is_terminal]

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get an order by ID, or None."""
        return self._orders.get(order_id)

    def _get_order(self, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Order {order_id} not found")
        return order

    def _transition(self, order: Order, new_state: OrderState) -> None:
        """Validate and apply a state transition."""
        if order.is_terminal:
            raise ValueError(
                f"Order {order.order_id} is in terminal state {order.state.value}"
            )
        allowed = VALID_TRANSITIONS.get(order.state, frozenset())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {order.state.value} → {new_state.value}"
            )
        order.state = new_state
