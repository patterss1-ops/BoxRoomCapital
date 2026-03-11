"""Engine A research dashboard context builders."""
from __future__ import annotations

from typing import Any, Callable

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactType, Engine


def _build_engine_a_regime_panel_context(
    *,
    artifact_store: ArtifactStore | None = None,
    latest_artifact_by_type: Callable[..., Any],
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    artifact = latest_artifact_by_type(
        ArtifactType.REGIME_SNAPSHOT,
        engine=Engine.ENGINE_A,
        artifact_store=artifact_store,
    )
    if artifact is None:
        return {"regime": None, "error": "No Engine A regime snapshot yet.", "generated_at": utc_now_iso()}

    payload = dict(artifact.body)
    payload["artifact_id"] = artifact.artifact_id
    payload["chain_id"] = artifact.chain_id
    payload["created_at"] = artifact.created_at
    return {"regime": payload, "error": "", "generated_at": utc_now_iso()}


def _build_engine_a_signal_heatmap_context(
    *,
    artifact_store: ArtifactStore | None = None,
    latest_artifact_by_type: Callable[..., Any],
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    artifact = latest_artifact_by_type(
        ArtifactType.ENGINE_A_SIGNAL_SET,
        engine=Engine.ENGINE_A,
        artifact_store=artifact_store,
    )
    if artifact is None:
        return {
            "rows": [],
            "signal_columns": ["trend", "carry", "value", "momentum"],
            "as_of": "",
            "error": "No Engine A signal set yet.",
            "generated_at": utc_now_iso(),
        }

    body = artifact.body
    signal_columns = ["trend", "carry", "value", "momentum"]
    grouped: dict[str, dict[str, Any]] = {}
    for key, payload in body.get("signals", {}).items():
        instrument, _, signal_type = key.partition(":")
        row = grouped.setdefault(
            instrument,
            {
                "instrument": instrument,
                "combined_forecast": None,
                "signals": {column: None for column in signal_columns},
            },
        )
        row["signals"][signal_type] = payload.get("normalized_value")
    for instrument, forecast in body.get("combined_forecast", {}).items():
        row = grouped.setdefault(
            instrument,
            {
                "instrument": instrument,
                "combined_forecast": None,
                "signals": {column: None for column in signal_columns},
            },
        )
        row["combined_forecast"] = forecast

    rows = sorted(
        grouped.values(),
        key=lambda row: abs(float(row["combined_forecast"] or 0.0)),
        reverse=True,
    )
    return {
        "rows": rows,
        "signal_columns": signal_columns,
        "as_of": body.get("as_of", ""),
        "artifact_id": artifact.artifact_id,
        "chain_id": artifact.chain_id,
        "error": "",
        "generated_at": utc_now_iso(),
    }


def _build_engine_a_portfolio_targets_context(
    *,
    artifact_store: ArtifactStore | None = None,
    latest_artifact_by_type: Callable[..., Any],
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    artifact = latest_artifact_by_type(
        ArtifactType.REBALANCE_SHEET,
        engine=Engine.ENGINE_A,
        artifact_store=artifact_store,
    )
    if artifact is None:
        return {"rows": [], "error": "No Engine A rebalance sheet yet.", "generated_at": utc_now_iso()}

    body = artifact.body
    instruments = sorted(
        set(body.get("current_positions", {}).keys())
        | set(body.get("target_positions", {}).keys())
        | set(body.get("deltas", {}).keys())
    )
    rows = [
        {
            "instrument": instrument,
            "current_position": body.get("current_positions", {}).get(instrument, 0.0),
            "target_position": body.get("target_positions", {}).get(instrument, 0.0),
            "delta": body.get("deltas", {}).get(instrument, 0.0),
        }
        for instrument in instruments
    ]
    return {
        "rows": rows,
        "approval_status": body.get("approval_status", ""),
        "estimated_cost": body.get("estimated_cost"),
        "artifact_id": artifact.artifact_id,
        "chain_id": artifact.chain_id,
        "created_at": artifact.created_at,
        "error": "",
        "generated_at": utc_now_iso(),
    }


def _build_engine_a_rebalance_panel_context(
    *,
    artifact_store: ArtifactStore | None = None,
    artifact_store_factory: Callable[[], ArtifactStore] | None = None,
    latest_artifact_by_type: Callable[..., Any],
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    if artifact_store is None:
        if artifact_store_factory is None:
            raise ValueError("artifact_store_factory is required when artifact_store is not provided")
        store = artifact_store_factory()
    else:
        store = artifact_store

    artifact = latest_artifact_by_type(
        ArtifactType.REBALANCE_SHEET,
        engine=Engine.ENGINE_A,
        artifact_store=store,
    )
    if artifact is None:
        return {"rebalance": None, "error": "No Engine A rebalance proposal yet.", "generated_at": utc_now_iso()}

    body = artifact.body
    chain = store.get_chain(artifact.chain_id) if hasattr(store, "get_chain") and artifact.chain_id else []
    executed = any(
        envelope.artifact_type == ArtifactType.EXECUTION_REPORT and int(envelope.version or 0) > int(artifact.version or 0)
        for envelope in chain
    )
    non_zero = {
        instrument: delta
        for instrument, delta in body.get("deltas", {}).items()
        if abs(float(delta or 0.0)) > 0
    }
    top_moves = sorted(non_zero.items(), key=lambda item: abs(float(item[1])), reverse=True)[:5]
    rebalance = {
        "artifact_id": artifact.artifact_id,
        "chain_id": artifact.chain_id,
        "created_at": artifact.created_at,
        "approval_status": body.get("approval_status", ""),
        "decision_source": body.get("decision_source") or "system",
        "decided_by": body.get("decided_by") or "",
        "operator_notes": body.get("operator_notes") or "",
        "estimated_cost": body.get("estimated_cost"),
        "move_count": len(non_zero),
        "executed": executed,
        "can_execute": len(non_zero) > 0 and not executed,
        "can_dismiss": not executed,
        "top_moves": [{"instrument": instrument, "delta": delta} for instrument, delta in top_moves],
    }
    return {"rebalance": rebalance, "error": "", "generated_at": utc_now_iso()}


def _build_engine_a_regime_journal_context(
    *,
    artifact_store: ArtifactStore | None = None,
    artifact_store_factory: Callable[[], ArtifactStore] | None = None,
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    if artifact_store is None:
        if artifact_store_factory is None:
            raise ValueError("artifact_store_factory is required when artifact_store is not provided")
        store = artifact_store_factory()
    else:
        store = artifact_store

    rows = store.query(
        artifact_type=ArtifactType.REGIME_JOURNAL,
        engine=Engine.ENGINE_A,
        limit=5,
    )
    entries = []
    for envelope in rows:
        body = envelope.body
        entries.append(
            {
                "artifact_id": envelope.artifact_id,
                "chain_id": envelope.chain_id,
                "as_of": body.get("as_of", ""),
                "summary": body.get("summary", ""),
                "key_changes": list(body.get("key_changes", [])),
                "risks": list(body.get("risks", [])),
                "created_at": envelope.created_at,
            }
        )
    return {
        "entries": entries,
        "error": "" if entries else "No regime journal entries yet.",
        "generated_at": utc_now_iso(),
    }
