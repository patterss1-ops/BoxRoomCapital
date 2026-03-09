from research.artifacts import ArtifactType
from research.engine_a.portfolio import TargetPosition
from research.engine_a.rebalancer import Rebalancer
from research.shared.cost_model import CostModel


def _targets():
    return {
        "ES": TargetPosition(
            instrument="ES",
            contracts=4,
            notional=20_000.0,
            weight=0.2,
            forecast=0.8,
            vol_contribution=0.03,
        ),
        "NQ": TargetPosition(
            instrument="NQ",
            contracts=-2,
            notional=-20_000.0,
            weight=-0.2,
            forecast=-0.5,
            vol_contribution=0.04,
        ),
    }


def test_generate_rebalance_computes_deltas():
    envelope = Rebalancer().generate_rebalance(
        current_positions={"ES": 1, "NQ": 0},
        target_positions=_targets(),
        cost_model=CostModel(),
    )

    assert envelope.artifact_type == ArtifactType.REBALANCE_SHEET
    assert envelope.body["deltas"]["ES"] == 3.0
    assert envelope.body["deltas"]["NQ"] == -2.0


def test_small_trade_filter_suppresses_noise():
    targets = {
        "ES": TargetPosition(
            instrument="ES",
            contracts=10,
            notional=50_000.0,
            weight=0.5,
            forecast=0.6,
            vol_contribution=0.05,
        )
    }

    envelope = Rebalancer(min_trade_ratio=0.15).generate_rebalance(
        current_positions={"ES": 9},
        target_positions=targets,
        cost_model=CostModel(),
    )

    assert envelope.body["deltas"]["ES"] == 0.0


def test_high_cost_rebalance_is_blocked():
    targets = {
        "CL": TargetPosition(
            instrument="CL",
            contracts=20,
            notional=200_000.0,
            weight=2.0,
            forecast=0.9,
            vol_contribution=0.12,
        )
    }

    envelope = Rebalancer(max_cost_pct=0.0001).generate_rebalance(
        current_positions={"CL": 0},
        target_positions=targets,
        cost_model=CostModel(),
        instrument_type="standard",
        broker="ibkr",
        asset_class="index",
    )

    assert envelope.body["approval_status"] == "blocked"


def test_no_trades_results_in_draft_status():
    envelope = Rebalancer().generate_rebalance(
        current_positions={"ES": 4, "NQ": -2},
        target_positions=_targets(),
        cost_model=CostModel(),
    )

    assert envelope.body["approval_status"] == "draft"
