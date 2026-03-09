"""Adapter that turns the existing backtester into Engine B experiment variants."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from analytics.backtester import Backtester, COST_MODE_ZERO
from research.artifacts import TestSpec
from research.engine_b.experiment import VariantResult


_FUTURES_TICKERS = {"ES", "NQ", "YM", "RTY", "ZN", "ZB", "ZF", "GC", "SI", "CL", "NG", "HG", "ZC", "ZS", "ZW", "6E", "6B", "6J"}
_CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "XRP"}


class ResearchBacktestAdapter:
    """Convert a TestSpec into one or more backtested experiment variants."""

    def __init__(self, backtester_factory: Callable[..., Backtester] | None = None):
        self._backtester_factory = backtester_factory or Backtester

    def __call__(self, test_spec: TestSpec) -> list[VariantResult]:
        dataset = test_spec.datasets[0]
        strategy_name = self._select_strategy(test_spec)
        instrument_type, broker, asset_class, caveats = self._cost_profile(test_spec, dataset.ticker)
        backtester = self._backtester_factory(
            lookback_days=0,
            cost_mode=COST_MODE_ZERO,
        )
        result = backtester.run(
            strategy_name,
            tickers=[dataset.ticker],
            start_date=dataset.start_date,
            end_date=dataset.end_date,
        )
        variant = VariantResult(
            name=f"{strategy_name.lower().replace(' ', '_')}:{dataset.ticker.lower()}",
            trades=self._convert_trades(result, instrument_type=instrument_type),
            params={
                "strategy_name": strategy_name,
                "ticker": dataset.ticker,
                "date_range": [dataset.start_date, dataset.end_date],
            },
            instrument_type=instrument_type,
            broker=broker,
            asset_class=asset_class,
            implementation_caveats=caveats + self._dataset_caveats(test_spec),
        )
        return [variant]

    @staticmethod
    def _select_strategy(test_spec: TestSpec) -> str:
        ticker = str(test_spec.datasets[0].ticker or "").strip().upper()
        hints = " ".join(
            list(test_spec.feature_list)
            + list(test_spec.baselines)
            + [test_spec.cost_model_ref]
        ).lower()
        if ticker in {"SPY", "TLT"} and ("rotation" in hints or "sector_relative" in hints):
            return "SPY/TLT Rotation v3"
        if ticker in _FUTURES_TICKERS and ("trend" in hints or "momentum" in hints or "carry" in hints):
            return "Trend Following v2"
        if ticker in _FUTURES_TICKERS:
            return "IBS++ Futures"
        if "trend" in hints or "momentum" in hints or "carry" in hints:
            return "Trend Following v2"
        return "IBS++ v3"

    @staticmethod
    def _cost_profile(test_spec: TestSpec, ticker: str) -> tuple[str, str, str, list[str]]:
        ref = str(test_spec.cost_model_ref or "").strip().lower()
        if ref == "ibkr_futures_standard_v1":
            return "standard", "ibkr", "futures", []
        if ref == "ig_index_v1":
            return "spread_bet", "ig", "index", []
        if ref == "kraken_spot_v1":
            return (
                "equity",
                "ibkr",
                "us",
                ["kraken spot cost template not implemented; approximated with ibkr_us_equity_v1"],
            )
        if str(ticker or "").strip().upper() in _CRYPTO_TICKERS:
            return (
                "equity",
                "ibkr",
                "us",
                ["crypto ticker routed through equity cost template for initial research validation"],
            )
        return "equity", "ibkr", "us", []

    @staticmethod
    def _dataset_caveats(test_spec: TestSpec) -> list[str]:
        caveats: list[str] = []
        if len(test_spec.datasets) > 1:
            caveats.append("only the primary dataset was used for the initial adapter run")
        if test_spec.datasets[0].frequency != "daily":
            caveats.append("adapter currently uses daily backtests regardless of requested frequency")
        if test_spec.search_budget > 1:
            caveats.append("search_budget currently maps to a single baseline backtest variant")
        return caveats

    @staticmethod
    def _convert_trades(result: Any, *, instrument_type: str) -> list[dict[str, Any]]:
        initial_equity = float(getattr(result, "initial_equity", 10_000.0) or 10_000.0)
        converted: list[dict[str, Any]] = []
        for trade in list(getattr(result, "trades", []) or []):
            if is_dataclass(trade):
                payload = asdict(trade)
            elif isinstance(trade, dict):
                payload = dict(trade)
            else:
                payload = vars(trade)
            entry_price = float(payload.get("entry_price") or 0.0)
            notional = max(abs(entry_price), 1.0)
            if instrument_type == "spread_bet":
                notional = max(notional * 10.0, 10.0)
            gross_pnl = float(payload.get("pnl_gross") or 0.0)
            gross_return = gross_pnl / notional if notional else 0.0
            converted.append(
                {
                    "gross_return": round(gross_return, 6),
                    "gross_pnl": round(gross_pnl, 6),
                    "notional": round(notional, 6),
                    "holding_days": int(payload.get("bars_held") or 0),
                    "entry_date": payload.get("entry_date"),
                    "exit_date": payload.get("exit_date"),
                    "entry_price": round(entry_price, 6),
                    "exit_price": round(float(payload.get("exit_price") or 0.0), 6),
                    "direction": payload.get("direction"),
                    "exit_reason": payload.get("exit_reason"),
                    "initial_equity": initial_equity,
                }
            )
        return converted
