from fund.promotion_gate import PromotionGateDecision
from research.artifacts import ArtifactType, PromotionOutcome
from research.engine_a.pipeline import EngineAPipeline
from research.engine_a.rebalancer import Rebalancer


class FakeStore:
    def __init__(self):
        self.saved = []
        self.items = {}

    def save(self, envelope):
        if envelope.artifact_id is None:
            envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        if envelope.chain_id is None:
            envelope.chain_id = f"chain-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


class MemoryFeatureCache:
    def __init__(self):
        self._store = {}
        self.invalidations = []

    def invalidate_stale_versions(self, instrument, as_of, signal_type, current_data_version):
        self.invalidations.append((instrument, as_of, signal_type, current_data_version))
        return 0

    def get_or_compute(self, instrument, as_of, signal_type, data_version, compute_fn):
        key = (instrument, as_of[:10], signal_type, data_version)
        if key not in self._store:
            payload = compute_fn()
            self._store[key] = {
                "instrument": instrument,
                "as_of": as_of[:10],
                "signal_type": signal_type,
                "data_version": data_version,
                **payload,
            }
        return self._store[key]


def _market_data_provider(as_of: str):
    return {
        "regime_inputs": {
            "vix": 18.0,
            "vix_percentile": 45.0,
            "index_data": {"trend_score": 1.2, "breadth": 0.62, "reversal_probability": 0.1},
            "yield_data": {"spread_bps": 120.0},
            "macro_data": {"credit_spread_bps": 110.0, "equity_drawdown_pct": -4.0},
        },
        "price_history": {
            "ES": [100 + i * 0.8 for i in range(300)],
            "NQ": [200 + i * 1.0 for i in range(300)],
        },
        "term_structure": {
            "ES": {"front_price": 5200.0, "deferred_price": 5170.0, "days_to_roll": 35, "carry_history": [-0.08, -0.02, 0.01, 0.05]},
            "NQ": {"front_price": 18200.0, "deferred_price": 18110.0, "days_to_roll": 35, "carry_history": [-0.05, 0.0, 0.03, 0.06]},
        },
        "value_history": {
            "ES": [1.2 + (i % 7) * 0.03 for i in range(300)],
            "NQ": [1.5 + (i % 9) * 0.02 for i in range(300)],
        },
        "current_value": {"ES": 1.45, "NQ": 1.7},
        "vol_estimates": {"ES": 0.16, "NQ": 0.22},
        "correlations": {
            "ES": {"ES": 1.0, "NQ": 0.75},
            "NQ": {"ES": 0.75, "NQ": 1.0},
        },
        "current_positions": {"ES": 0, "NQ": 0},
        "capital": 100_000.0,
        "contract_sizes": {"ES": 5_000.0, "NQ": 10_000.0},
        "instrument_type": "mini_equity",
        "broker": "ibkr",
        "asset_class": "index",
    }


def _gate(allowed: bool):
    return lambda **kwargs: PromotionGateDecision(
        allowed=allowed,
        reason_code="PROMOTION_GATE_PASSED" if allowed else "NO_LIVE_SET",
        message="ok" if allowed else "blocked",
        strategy_key="engine_a",
        outcome=PromotionOutcome.PROMOTE if allowed else PromotionOutcome.REJECT,
    )


def test_engine_a_pipeline_happy_path_creates_artifact_chain():
    store = FakeStore()
    pipeline = EngineAPipeline(
        artifact_store=store,
        market_data_provider=_market_data_provider,
        promotion_gate=_gate(True),
        strategy_key="engine_a",
    )

    result = pipeline.run_daily("2026-03-09T00:00:00Z")

    assert result.gate_decision.allowed is True
    assert len(result.artifacts) == 5
    assert [artifact.artifact_type for artifact in result.artifacts] == [
        ArtifactType.REGIME_SNAPSHOT,
        ArtifactType.ENGINE_A_SIGNAL_SET,
        ArtifactType.REBALANCE_SHEET,
        ArtifactType.TRADE_SHEET,
        ArtifactType.EXECUTION_REPORT,
    ]
    assert len({artifact.chain_id for artifact in result.artifacts}) == 1
    assert set(result.forecasts.keys()) == {"ES", "NQ"}


def test_engine_a_pipeline_stops_before_execution_when_gate_blocks():
    store = FakeStore()
    pipeline = EngineAPipeline(
        artifact_store=store,
        market_data_provider=_market_data_provider,
        promotion_gate=_gate(False),
        strategy_key="engine_a",
    )

    result = pipeline.run_daily("2026-03-09T00:00:00Z")

    assert result.gate_decision.allowed is False
    assert [artifact.artifact_type for artifact in result.artifacts] == [
        ArtifactType.REGIME_SNAPSHOT,
        ArtifactType.ENGINE_A_SIGNAL_SET,
        ArtifactType.REBALANCE_SHEET,
    ]


def test_engine_a_pipeline_respects_rebalance_cost_block():
    store = FakeStore()
    pipeline = EngineAPipeline(
        artifact_store=store,
        market_data_provider=_market_data_provider,
        promotion_gate=_gate(True),
        rebalancer=Rebalancer(max_cost_pct=0.000001),
        strategy_key="engine_a",
    )

    result = pipeline.run_daily("2026-03-09T00:00:00Z")

    assert result.gate_decision.allowed is True
    assert result.artifacts[-1].artifact_type == ArtifactType.REBALANCE_SHEET
    assert result.artifacts[-1].body["approval_status"] == "blocked"


def test_engine_a_pipeline_uses_feature_cache_to_avoid_recompute():
    store = FakeStore()
    cache = MemoryFeatureCache()
    counts = {"trend": 0, "carry": 0, "value": 0, "momentum": 0}

    class CountingTrend:
        def compute(self, prices):
            counts["trend"] += 1
            return 0.4

    class CountingCarry:
        def compute(self, **kwargs):
            counts["carry"] += 1
            return 0.2

    class CountingValue:
        def compute(self, **kwargs):
            counts["value"] += 1
            return 0.1

    class CountingMomentum:
        def compute(self, prices):
            counts["momentum"] += 1
            return 0.3

    pipeline = EngineAPipeline(
        artifact_store=store,
        market_data_provider=_market_data_provider,
        promotion_gate=_gate(False),
        strategy_key="engine_a",
        feature_cache=cache,
        trend_signal=CountingTrend(),
        carry_signal=CountingCarry(),
        value_signal=CountingValue(),
        momentum_signal=CountingMomentum(),
    )

    pipeline.run_daily("2026-03-09T00:00:00Z")
    pipeline.run_daily("2026-03-09T12:00:00Z")

    assert counts == {"trend": 2, "carry": 2, "value": 2, "momentum": 2}
