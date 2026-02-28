"""
Risk management — pre-trade risk gate and portfolio-level limits.

A-006: Provides hierarchical risk limits (fund → sleeve → strategy → trade)
evaluated before every broker submission. No order reaches a broker without
passing all risk rules.
"""
from risk.pre_trade_gate import (
    RiskLimits,
    RiskVerdict,
    PreTradeRiskGate,
)

__all__ = [
    "RiskLimits",
    "RiskVerdict",
    "PreTradeRiskGate",
]
