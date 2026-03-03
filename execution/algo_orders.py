"""TWAP/VWAP Execution Algorithms — M-001.

Provides smart execution algorithms that split large orders into smaller
child slices to minimise market impact.  Supports Time-Weighted Average
Price (TWAP) and Volume-Weighted Average Price (VWAP) strategies.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AlgoType(str, Enum):
    """Supported algorithm types."""
    TWAP = "TWAP"
    VWAP = "VWAP"


class SliceStatus(str, Enum):
    """Lifecycle status for an individual order slice."""
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AlgoOrderConfig:
    """Configuration for an algorithmic order."""

    algo_type: AlgoType
    ticker: str
    side: str  # "BUY" or "SELL"
    total_qty: float
    duration_seconds: int
    num_slices: int
    urgency: float = 0.5
    price_limit: Optional[float] = None


@dataclass
class OrderSlice:
    """A single child slice within an algorithmic order."""

    slice_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    slice_index: int = 0
    target_qty: float = 0.0
    filled_qty: float = 0.0
    avg_price: float = 0.0
    status: SliceStatus = SliceStatus.PENDING
    scheduled_at: str = ""
    executed_at: str = ""
    error: str = ""


@dataclass
class AlgoOrderState:
    """Full state for an algorithmic order and its child slices."""

    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: AlgoOrderConfig = field(default_factory=lambda: AlgoOrderConfig(
        algo_type=AlgoType.TWAP, ticker="", side="BUY",
        total_qty=0, duration_seconds=0, num_slices=0,
    ))
    slices: list[OrderSlice] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "ACTIVE"
    total_filled: float = 0.0
    avg_fill_price: float = 0.0
    completion_pct: float = 0.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class AlgoExecutionEngine:
    """Create, manage and fill TWAP / VWAP algorithmic orders."""

    def __init__(self) -> None:
        self._orders: dict[str, AlgoOrderState] = {}

    # -- Order creation -----------------------------------------------------

    def create_order(self, config: AlgoOrderConfig) -> AlgoOrderState:
        """Create a new algo order and compute its slice schedule.

        * **TWAP** — equal-sized slices evenly spaced across the duration.
        * **VWAP** — front-loaded distribution: 30 % in the first third,
          50 % in the middle third, 20 % in the last third of slices.
        """
        now = datetime.now(timezone.utc)
        num = max(config.num_slices, 1)
        interval = config.duration_seconds / num

        # Compute per-slice quantities
        quantities = self._compute_quantities(config.algo_type, config.total_qty, num)

        slices: list[OrderSlice] = []
        for i in range(num):
            scheduled = now + timedelta(seconds=interval * i)
            slices.append(OrderSlice(
                slice_index=i,
                target_qty=quantities[i],
                scheduled_at=scheduled.isoformat(),
            ))

        state = AlgoOrderState(
            config=config,
            slices=slices,
            created_at=now.isoformat(),
        )
        self._orders[state.order_id] = state
        logger.info(
            "Created %s order %s for %s %s x%.2f (%d slices over %ds)",
            config.algo_type.value, state.order_id, config.side,
            config.ticker, config.total_qty, num, config.duration_seconds,
        )
        return state

    # -- Queries ------------------------------------------------------------

    def get_order(self, order_id: str) -> AlgoOrderState | None:
        """Return an order by *order_id*, or ``None`` if not found."""
        return self._orders.get(order_id)

    def list_orders(self) -> list[AlgoOrderState]:
        """Return all tracked orders."""
        return list(self._orders.values())

    def get_next_slice(self, order_id: str) -> OrderSlice | None:
        """Return the next PENDING slice for the order, or ``None``."""
        state = self._require_order(order_id)
        for s in state.slices:
            if s.status == SliceStatus.PENDING:
                return s
        return None

    # -- Mutations ----------------------------------------------------------

    def fill_slice(
        self,
        order_id: str,
        slice_index: int,
        filled_qty: float,
        price: float,
    ) -> OrderSlice:
        """Record a fill (or partial fill) for a specific slice.

        Raises ``ValueError`` when the fill price breaches the configured
        price limit (if any).
        """
        state = self._require_order(order_id)
        sl = self._get_slice(state, slice_index)

        # Price-limit guard
        cfg = state.config
        if cfg.price_limit is not None:
            if cfg.side == "BUY" and price > cfg.price_limit:
                sl.status = SliceStatus.FAILED
                sl.error = f"price {price} exceeds buy limit {cfg.price_limit}"
                raise ValueError(sl.error)
            if cfg.side == "SELL" and price < cfg.price_limit:
                sl.status = SliceStatus.FAILED
                sl.error = f"price {price} below sell limit {cfg.price_limit}"
                raise ValueError(sl.error)

        # Weighted average price when adding to an existing partial fill
        if sl.filled_qty > 0:
            total_cost = sl.avg_price * sl.filled_qty + price * filled_qty
            sl.filled_qty += filled_qty
            sl.avg_price = total_cost / sl.filled_qty
        else:
            sl.filled_qty = filled_qty
            sl.avg_price = price

        if sl.filled_qty >= sl.target_qty:
            sl.status = SliceStatus.FILLED
        else:
            sl.status = SliceStatus.PARTIALLY_FILLED

        sl.executed_at = datetime.now(timezone.utc).isoformat()

        # Auto-update aggregate fields
        self.update_completion(order_id)
        return sl

    def cancel_order(self, order_id: str) -> AlgoOrderState:
        """Cancel all remaining unfilled slices.

        If the order is already in a terminal state (COMPLETED / CANCELLED /
        FAILED) this is a no-op.
        """
        state = self._require_order(order_id)
        if state.status in ("COMPLETED", "CANCELLED", "FAILED"):
            return state

        for sl in state.slices:
            if sl.status in (SliceStatus.PENDING, SliceStatus.EXECUTING):
                sl.status = SliceStatus.CANCELLED

        state.status = "CANCELLED"
        self.update_completion(order_id)
        return state

    def update_completion(self, order_id: str) -> AlgoOrderState:
        """Recalculate *total_filled*, *avg_fill_price*, *completion_pct*
        and derive the overall order status from slice states.
        """
        state = self._require_order(order_id)

        total_filled = 0.0
        weighted_cost = 0.0
        for sl in state.slices:
            total_filled += sl.filled_qty
            weighted_cost += sl.avg_price * sl.filled_qty

        state.total_filled = total_filled
        if total_filled > 0:
            state.avg_fill_price = weighted_cost / total_filled
        else:
            state.avg_fill_price = 0.0

        if state.config.total_qty > 0:
            state.completion_pct = (total_filled / state.config.total_qty) * 100.0
        else:
            state.completion_pct = 0.0

        # Derive status — only upgrade to COMPLETED if not already cancelled/failed
        if state.status not in ("CANCELLED", "FAILED"):
            if total_filled >= state.config.total_qty and state.config.total_qty > 0:
                state.status = "COMPLETED"
            elif any(sl.status == SliceStatus.FAILED for sl in state.slices):
                state.status = "FAILED"
            else:
                state.status = "ACTIVE"

        return state

    # -- Internals ----------------------------------------------------------

    def _require_order(self, order_id: str) -> AlgoOrderState:
        state = self._orders.get(order_id)
        if state is None:
            raise KeyError(f"Order {order_id} not found")
        return state

    @staticmethod
    def _get_slice(state: AlgoOrderState, slice_index: int) -> OrderSlice:
        for sl in state.slices:
            if sl.slice_index == slice_index:
                return sl
        raise KeyError(f"Slice {slice_index} not found in order {state.order_id}")

    @staticmethod
    def _compute_quantities(
        algo_type: AlgoType,
        total_qty: float,
        num_slices: int,
    ) -> list[float]:
        """Return per-slice target quantities.

        * TWAP: equal sizes.
        * VWAP: 30 % first third, 50 % middle third, 20 % last third.
        """
        if num_slices <= 0:
            return []

        if algo_type == AlgoType.TWAP:
            base = total_qty / num_slices
            quantities = [base] * num_slices
            return quantities

        # VWAP: split slices into three buckets
        first_count = num_slices // 3 or 1
        last_count = num_slices // 3 or 1
        mid_count = num_slices - first_count - last_count
        if mid_count <= 0:
            mid_count = 0
            # Re-balance: with very few slices give first_count most
            if num_slices == 1:
                return [total_qty]
            if num_slices == 2:
                return [total_qty * 0.6, total_qty * 0.4]
            # For 3 slices, first_count=1, mid_count adjusted
            last_count = num_slices - first_count
            mid_count = 0
            # Distribute 30/70 between first and last buckets
            first_alloc = total_qty * 0.3
            last_alloc = total_qty * 0.7
            quantities: list[float] = []
            for _ in range(first_count):
                quantities.append(first_alloc / first_count)
            for _ in range(last_count):
                quantities.append(last_alloc / last_count)
            return quantities

        first_alloc = total_qty * 0.3
        mid_alloc = total_qty * 0.5
        last_alloc = total_qty * 0.2

        quantities = []
        for _ in range(first_count):
            quantities.append(first_alloc / first_count)
        for _ in range(mid_count):
            quantities.append(mid_alloc / mid_count)
        for _ in range(last_count):
            quantities.append(last_alloc / last_count)

        return quantities
