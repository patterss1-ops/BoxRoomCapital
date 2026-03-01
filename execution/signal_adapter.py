"""Convert strategy Signal objects into broker-agnostic OrderIntent envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from execution.order_intent import OrderIntent, OrderSide, OrderType
from strategies.base import Signal, SignalType


@dataclass(frozen=True)
class StrategySlotConfig:
    """Execution routing config for one strategy slot.

    Each slot binds a strategy to a sleeve, broker, account type, and base
    position size.  The orchestrator holds one slot per (strategy, ticker) pair.
    """

    strategy_id: str
    strategy_version: str
    sleeve: str
    account_type: str       # RouteAccountType value, e.g. "SPREADBET"
    broker_target: str      # e.g. "ig", "ibkr"
    base_qty: float         # Base position size, scaled by signal.size_multiplier
    risk_tags: list[str] = field(default_factory=list)


# Map signal types to (side, is_exit)
_SIGNAL_SIDE_MAP: dict[SignalType, tuple[OrderSide, bool]] = {
    SignalType.LONG_ENTRY: (OrderSide.BUY, False),
    SignalType.LONG_EXIT: (OrderSide.SELL, True),
    SignalType.SHORT_ENTRY: (OrderSide.SELL, False),
    SignalType.SHORT_EXIT: (OrderSide.BUY, True),
}


def signal_to_order_intent(
    signal: Signal,
    slot: StrategySlotConfig,
    ticker_metadata: Optional[dict[str, Any]] = None,
) -> OrderIntent:
    """Convert a strategy Signal into an OrderIntent.

    Args:
        signal: Signal from a strategy's generate_signal() call.
        slot: Execution config for this strategy slot.
        ticker_metadata: Optional MARKET_MAP entry for epic/instrument metadata.

    Returns:
        OrderIntent ready for risk gate and order intent store.

    Raises:
        ValueError: If signal type is NONE or unrecognised.
    """
    if signal.signal_type == SignalType.NONE:
        raise ValueError("Cannot convert a NONE signal to an OrderIntent")

    mapping = _SIGNAL_SIDE_MAP.get(signal.signal_type)
    if mapping is None:
        raise ValueError(f"Unrecognised signal type: {signal.signal_type}")

    side, is_exit = mapping
    qty = slot.base_qty * signal.size_multiplier

    metadata: dict[str, Any] = {}
    if ticker_metadata:
        metadata.update(ticker_metadata)
    metadata["signal_reason"] = signal.reason
    metadata["is_exit"] = is_exit
    if signal.timestamp is not None:
        metadata["signal_timestamp"] = str(signal.timestamp)

    return OrderIntent(
        strategy_id=slot.strategy_id,
        strategy_version=slot.strategy_version,
        sleeve=slot.sleeve,
        account_type=slot.account_type,
        broker_target=slot.broker_target,
        instrument=signal.ticker,
        side=side.value,
        qty=qty,
        order_type=OrderType.MARKET.value,
        risk_tags=list(slot.risk_tags),
        metadata=metadata,
    )
