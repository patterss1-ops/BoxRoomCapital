"""Deterministic transaction-cost templates for research backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostEstimate:
    entry_cost: float
    exit_cost: float
    holding_cost: float
    slippage_estimate: float
    total_round_trip: float
    total_as_pct: float
    cost_template: str


class CostModel:
    """Asset-class-specific cost templates."""

    IG_COSTS = {
        "uk_equity": {"spread_bps": 10.0, "funding_daily_bps": 2.5, "min_spread_gbp": 0.5, "slippage_bps": 2.0},
        "us_equity": {"spread_bps": 8.0, "funding_daily_bps": 2.5, "min_spread_gbp": 0.3, "slippage_bps": 1.5},
        "index": {"spread_bps": 6.0, "funding_daily_bps": 1.5, "min_spread_gbp": 0.0, "slippage_bps": 1.0},
        "commodity": {"spread_bps": 15.0, "funding_daily_bps": 3.0, "min_spread_gbp": 0.0, "slippage_bps": 2.5},
        "fx": {"spread_bps": 3.0, "funding_daily_bps": 0.5, "min_spread_gbp": 0.0, "slippage_bps": 0.5},
    }

    IBKR_FUTURES = {
        "micro_equity": {"commission_per_side": 0.62, "exchange_fee": 0.25, "slippage_bps": 1.5},
        "mini_equity": {"commission_per_side": 1.18, "exchange_fee": 0.50, "slippage_bps": 1.0},
        "standard": {"commission_per_side": 2.25, "exchange_fee": 1.00, "slippage_bps": 0.75},
    }

    IBKR_EQUITY = {
        "us": {"commission_pct": 0.0035, "min_commission": 0.35, "max_commission_pct": 0.5, "slippage_bps": 1.0},
        "uk": {"commission_pct": 0.05, "min_commission": 3.0, "slippage_bps": 1.5},
    }

    def estimate_round_trip_cost(
        self,
        instrument_type: str,
        broker: str,
        notional: float,
        holding_days: int,
        asset_class: str,
    ) -> CostEstimate:
        if notional <= 0:
            raise ValueError("notional must be positive")
        if holding_days < 0:
            raise ValueError("holding_days cannot be negative")

        broker_key = broker.lower()
        if broker_key == "ig":
            cfg = self.IG_COSTS[asset_class]
            entry_cost = max(notional * cfg["spread_bps"] / 10_000, cfg["min_spread_gbp"])
            exit_cost = max(notional * cfg["spread_bps"] / 10_000, cfg["min_spread_gbp"])
            holding_cost = notional * cfg["funding_daily_bps"] / 10_000 * holding_days
            slippage_estimate = notional * cfg["slippage_bps"] / 10_000
            template = f"ig:{asset_class}"
        elif broker_key == "ibkr" and instrument_type in self.IBKR_FUTURES:
            cfg = self.IBKR_FUTURES[instrument_type]
            entry_cost = cfg["commission_per_side"] + cfg["exchange_fee"]
            exit_cost = cfg["commission_per_side"] + cfg["exchange_fee"]
            holding_cost = 0.0
            slippage_estimate = notional * cfg["slippage_bps"] / 10_000
            template = f"ibkr:futures:{instrument_type}"
        elif broker_key == "ibkr" and asset_class in self.IBKR_EQUITY:
            cfg = self.IBKR_EQUITY[asset_class]
            commission = max(
                notional * cfg["commission_pct"] / 100,
                cfg["min_commission"],
            )
            max_pct = cfg.get("max_commission_pct")
            if max_pct is not None:
                commission = min(commission, notional * max_pct / 100)
            entry_cost = commission
            exit_cost = commission
            holding_cost = 0.0
            slippage_estimate = notional * cfg["slippage_bps"] / 10_000
            template = f"ibkr:equity:{asset_class}"
        else:
            raise ValueError(
                f"Unsupported broker/instrument combination: broker={broker} instrument_type={instrument_type} asset_class={asset_class}"
            )

        total_round_trip = entry_cost + exit_cost + holding_cost + slippage_estimate
        total_as_pct = total_round_trip / notional
        return CostEstimate(
            entry_cost=round(entry_cost, 6),
            exit_cost=round(exit_cost, 6),
            holding_cost=round(holding_cost, 6),
            slippage_estimate=round(slippage_estimate, 6),
            total_round_trip=round(total_round_trip, 6),
            total_as_pct=round(total_as_pct, 6),
            cost_template=template,
        )

    def apply_to_backtest(
        self,
        trades: list[dict],
        instrument_type: str,
        broker: str,
        asset_class: str,
    ) -> list[dict]:
        adjusted: list[dict] = []
        for trade in trades:
            notional = float(trade["notional"])
            holding_days = int(trade.get("holding_days", 0))
            estimate = self.estimate_round_trip_cost(
                instrument_type=instrument_type,
                broker=broker,
                notional=notional,
                holding_days=holding_days,
                asset_class=asset_class,
            )
            updated = dict(trade)
            updated["cost_estimate"] = asdict(estimate)
            if "gross_pnl" in updated:
                updated["net_pnl"] = round(float(updated["gross_pnl"]) - estimate.total_round_trip, 6)
            if "gross_return" in updated:
                gross_return = float(updated["gross_return"])
                cost_return = estimate.total_as_pct if abs(gross_return) <= 1.5 else estimate.total_as_pct * 100
                updated["net_return"] = round(gross_return - cost_return, 6)
            adjusted.append(updated)
        return adjusted
