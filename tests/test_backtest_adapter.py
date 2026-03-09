from types import SimpleNamespace

from research.artifacts import TestSpec
from research.shared.backtest_adapter import ResearchBacktestAdapter


class FakeBacktester:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def run(self, strategy_name, tickers, start_date, end_date):
        assert start_date == "2020-01-01"
        assert end_date == "2025-12-31"
        return SimpleNamespace(
            initial_equity=10_000.0,
            trades=[
                SimpleNamespace(
                    entry_price=100.0,
                    exit_price=105.0,
                    pnl_gross=5.0,
                    bars_held=4,
                    entry_date="2025-01-03",
                    exit_date="2025-01-07",
                    direction="BUY",
                    exit_reason="target",
                )
            ],
            strategy=strategy_name,
            tickers=tickers,
        )


def _test_spec(**overrides):
    payload = {
        "hypothesis_ref": "hyp-1",
        "datasets": [
            {
                "name": "prices",
                "ticker": "AAPL",
                "start_date": "2020-01-01",
                "end_date": "2025-12-31",
                "frequency": "daily",
                "point_in_time": True,
            }
        ],
        "feature_list": ["source_credibility"],
        "train_split": {"start_date": "2020-01-01", "end_date": "2023-12-31"},
        "validation_split": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
        "test_split": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
        "baselines": ["buy_and_hold"],
        "search_budget": 3,
        "cost_model_ref": "ibkr_us_equity_v1",
        "eval_metrics": ["sharpe", "profit_factor", "max_drawdown"],
        "frozen_at": "2026-03-08T23:00:00Z",
    }
    payload.update(overrides)
    return TestSpec.model_validate(payload)


def test_backtest_adapter_returns_variant_results():
    adapter = ResearchBacktestAdapter(backtester_factory=FakeBacktester)

    variants = adapter(_test_spec())

    assert len(variants) == 1
    assert variants[0].name == "ibs++_v3:aapl"
    assert variants[0].broker == "ibkr"
    assert variants[0].asset_class == "us"
    assert variants[0].trades[0]["gross_return"] == 0.05
    assert variants[0].implementation_caveats[-1] == "search_budget currently maps to a single baseline backtest variant"


def test_backtest_adapter_maps_futures_trend_specs():
    adapter = ResearchBacktestAdapter(backtester_factory=FakeBacktester)

    variants = adapter(
        _test_spec(
            datasets=[
                {
                    "name": "prices",
                    "ticker": "ES",
                    "start_date": "2020-01-01",
                    "end_date": "2025-12-31",
                    "frequency": "daily",
                    "point_in_time": True,
                }
            ],
            feature_list=["trend_strength", "carry"],
            cost_model_ref="ibkr_futures_standard_v1",
        )
    )

    assert variants[0].params["strategy_name"] == "Trend Following v2"
    assert variants[0].instrument_type == "standard"
    assert variants[0].broker == "ibkr"
