import pytest

from research.shared.cost_model import CostModel


def test_ig_equity_costs_include_spread_funding_and_slippage():
    model = CostModel()

    estimate = model.estimate_round_trip_cost(
        instrument_type="cfd",
        broker="ig",
        notional=10_000.0,
        holding_days=5,
        asset_class="uk_equity",
    )

    assert estimate.entry_cost == 10.0
    assert estimate.exit_cost == 10.0
    assert estimate.holding_cost == 12.5
    assert estimate.slippage_estimate == 2.0
    assert estimate.total_round_trip == 34.5
    assert estimate.cost_template == "ig:uk_equity"


def test_ibkr_futures_costs_use_per_side_commissions():
    model = CostModel()

    estimate = model.estimate_round_trip_cost(
        instrument_type="micro_equity",
        broker="ibkr",
        notional=25_000.0,
        holding_days=7,
        asset_class="index",
    )

    assert estimate.entry_cost == 0.87
    assert estimate.exit_cost == 0.87
    assert estimate.holding_cost == 0.0
    assert estimate.slippage_estimate == 3.75
    assert estimate.total_round_trip == 5.49


def test_ibkr_equity_costs_respect_minimum_commission():
    model = CostModel()

    estimate = model.estimate_round_trip_cost(
        instrument_type="equity",
        broker="ibkr",
        notional=5_000.0,
        holding_days=3,
        asset_class="us",
    )

    assert estimate.entry_cost == 0.35
    assert estimate.exit_cost == 0.35
    assert estimate.slippage_estimate == 0.5
    assert estimate.total_round_trip == 1.2


def test_holding_cost_increases_with_time():
    model = CostModel()

    short_hold = model.estimate_round_trip_cost("cfd", "ig", 10_000.0, 1, "index")
    long_hold = model.estimate_round_trip_cost("cfd", "ig", 10_000.0, 10, "index")

    assert long_hold.holding_cost > short_hold.holding_cost
    assert long_hold.total_round_trip > short_hold.total_round_trip


def test_apply_to_backtest_adds_net_return_and_net_pnl():
    model = CostModel()

    adjusted = model.apply_to_backtest(
        trades=[
            {
                "trade_id": "t-1",
                "notional": 10_000.0,
                "holding_days": 5,
                "gross_return": 0.05,
                "gross_pnl": 500.0,
            }
        ],
        instrument_type="cfd",
        broker="ig",
        asset_class="uk_equity",
    )

    assert adjusted[0]["net_return"] < 0.05
    assert adjusted[0]["net_pnl"] == pytest.approx(465.5)
    assert adjusted[0]["cost_estimate"]["total_round_trip"] == 34.5


def test_invalid_cost_request_raises():
    model = CostModel()

    with pytest.raises(ValueError):
        model.estimate_round_trip_cost("unknown", "nowhere", 10_000.0, 1, "mystery")
