from app.api.server import (
    _build_engine_a_portfolio_targets_context,
    _build_engine_a_rebalance_panel_context,
    _build_engine_a_regime_journal_context,
    _build_engine_a_regime_panel_context,
    _build_engine_a_signal_heatmap_context,
)
from research.artifacts import ArtifactEnvelope, ArtifactStatus, ArtifactType, Engine


class FakeArtifactStore:
    def __init__(self, mapping):
        self._mapping = mapping

    def query(self, artifact_type=None, engine=None, limit=50, **kwargs):
        return list(self._mapping.get((artifact_type, engine), []))[:limit]


def test_engine_a_signal_heatmap_context_groups_signals_by_instrument():
    signal_artifact = ArtifactEnvelope(
        artifact_id="artifact-signal",
        chain_id="chain-a",
        version=2,
        artifact_type=ArtifactType.ENGINE_A_SIGNAL_SET,
        engine=Engine.ENGINE_A,
        status=ArtifactStatus.ACTIVE,
        created_at="2026-03-09T08:10:00Z",
        created_by="tester",
        body={
            "as_of": "2026-03-09T08:00:00Z",
            "signals": {
                "ES:trend": {"normalized_value": 0.7},
                "ES:carry": {"normalized_value": -0.2},
                "ES:value": {"normalized_value": 0.1},
                "ES:momentum": {"normalized_value": 0.5},
                "NQ:trend": {"normalized_value": 0.4},
            },
            "forecast_weights": {"trend": 0.35},
            "combined_forecast": {"ES": 0.31, "NQ": 0.12},
            "regime_ref": "artifact-regime",
        },
    )
    store = FakeArtifactStore({(ArtifactType.ENGINE_A_SIGNAL_SET, Engine.ENGINE_A): [signal_artifact]})

    context = _build_engine_a_signal_heatmap_context(artifact_store=store)

    assert context["error"] == ""
    assert context["rows"][0]["instrument"] == "ES"
    assert context["rows"][0]["signals"]["trend"] == 0.7
    assert context["rows"][0]["combined_forecast"] == 0.31
    assert context["signal_columns"] == ["trend", "carry", "value", "momentum"]


def test_engine_a_rebalance_contexts_render_latest_positions_and_moves():
    rebalance_artifact = ArtifactEnvelope(
        artifact_id="artifact-rebalance",
        chain_id="chain-a",
        version=3,
        artifact_type=ArtifactType.REBALANCE_SHEET,
        engine=Engine.ENGINE_A,
        status=ArtifactStatus.ACTIVE,
        created_at="2026-03-09T08:11:00Z",
        created_by="tester",
        body={
            "as_of": "2026-03-09T08:00:00Z",
            "current_positions": {"ES": 1.0, "NQ": 0.0},
            "target_positions": {"ES": 2.0, "NQ": -1.0},
            "deltas": {"ES": 1.0, "NQ": -1.0},
            "estimated_cost": 0.0042,
            "approval_status": "approved",
        },
    )
    store = FakeArtifactStore({(ArtifactType.REBALANCE_SHEET, Engine.ENGINE_A): [rebalance_artifact]})

    targets = _build_engine_a_portfolio_targets_context(artifact_store=store)
    rebalance = _build_engine_a_rebalance_panel_context(artifact_store=store)

    assert targets["rows"][0]["instrument"] == "ES"
    assert targets["rows"][1]["instrument"] == "NQ"
    assert rebalance["rebalance"]["move_count"] == 2
    assert rebalance["rebalance"]["top_moves"][0]["instrument"] in {"ES", "NQ"}
    assert rebalance["rebalance"]["approval_status"] == "approved"
    assert rebalance["rebalance"]["decision_source"] == "system"
    assert rebalance["rebalance"]["can_execute"] is True
    assert rebalance["rebalance"]["can_dismiss"] is True


def test_engine_a_regime_panel_and_journal_contexts_use_latest_artifacts():
    regime_artifact = ArtifactEnvelope(
        artifact_id="artifact-regime",
        chain_id="chain-a",
        version=1,
        artifact_type=ArtifactType.REGIME_SNAPSHOT,
        engine=Engine.ENGINE_A,
        status=ArtifactStatus.ACTIVE,
        created_at="2026-03-09T08:09:00Z",
        created_by="tester",
        body={
            "as_of": "2026-03-09T08:00:00Z",
            "vol_regime": "normal",
            "trend_regime": "strong_trend",
            "carry_regime": "steep",
            "macro_regime": "risk_on",
            "sizing_factor": 0.9,
            "active_overrides": [],
            "indicators": {"vix": 18.0},
        },
    )
    journal_artifact = ArtifactEnvelope(
        artifact_id="artifact-journal",
        chain_id="chain-a",
        version=2,
        artifact_type=ArtifactType.REGIME_JOURNAL,
        engine=Engine.ENGINE_A,
        status=ArtifactStatus.ACTIVE,
        created_at="2026-03-09T08:12:00Z",
        created_by="tester",
        body={
            "as_of": "2026-03-09T08:00:00Z",
            "regime_snapshot_ref": "artifact-regime",
            "summary": "Trend and carry remain supportive.",
            "key_changes": ["carry firmed"],
            "risks": ["crowded long equity"],
        },
    )
    store = FakeArtifactStore(
        {
            (ArtifactType.REGIME_SNAPSHOT, Engine.ENGINE_A): [regime_artifact],
            (ArtifactType.REGIME_JOURNAL, Engine.ENGINE_A): [journal_artifact],
        }
    )

    regime = _build_engine_a_regime_panel_context(artifact_store=store)
    journal = _build_engine_a_regime_journal_context(artifact_store=store)

    assert regime["regime"]["macro_regime"] == "risk_on"
    assert regime["regime"]["artifact_id"] == "artifact-regime"
    assert journal["entries"][0]["summary"] == "Trend and carry remain supportive."
    assert journal["entries"][0]["key_changes"] == ["carry firmed"]
