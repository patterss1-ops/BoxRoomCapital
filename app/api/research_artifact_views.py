"""Research artifact serialization and chain-view helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.api.shared import _relative_time_label
from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType
from research.manual_execution import find_chain_artifact as _manual_find_chain_artifact
from utils.datetime_utils import utc_now_iso as _utc_now_iso


def _artifact_body_summary_fields(artifact_type: ArtifactType) -> list[tuple[str, str]]:
    return {
        ArtifactType.EVENT_CARD: [
            ("Source", "source_class"),
            ("Materiality", "materiality"),
            ("Sensitivity", "time_sensitivity"),
            ("Instruments", "affected_instruments"),
            ("Claims", "claims"),
        ],
        ArtifactType.HYPOTHESIS_CARD: [
            ("Direction", "direction"),
            ("Horizon", "horizon"),
            ("Confidence", "confidence"),
            ("Catalyst", "catalyst"),
            ("Expressions", "candidate_expressions"),
        ],
        ArtifactType.FALSIFICATION_MEMO: [
            ("Alternative", "cheapest_alternative"),
            ("Unresolved", "unresolved_objections"),
            ("Resolved", "resolved_objections"),
            ("Crowding", "crowding_check.crowding_level"),
            ("Challenge Model", "challenge_model"),
        ],
        ArtifactType.SCORING_RESULT: [
            ("Outcome", "outcome"),
            ("Next Stage", "next_stage"),
            ("Final Score", "final_score"),
            ("Raw Total", "raw_total"),
            ("Blocking", "blocking_objections"),
            ("Reason", "outcome_reason"),
        ],
        ArtifactType.REVIEW_TRIGGER: [
            ("Strategy", "strategy_id"),
            ("Health", "health_status"),
            ("Recommended", "recommended_action"),
            ("Flags", "flags"),
            ("Ack", "operator_ack"),
        ],
        ArtifactType.RETIREMENT_MEMO: [
            ("Trigger", "trigger"),
            ("Final Status", "final_status"),
            ("Hypothesis", "hypothesis_ref"),
            ("Lessons", "lessons"),
        ],
        ArtifactType.REGIME_SNAPSHOT: [
            ("Macro", "macro_regime"),
            ("Vol", "vol_regime"),
            ("Trend", "trend_regime"),
            ("Carry", "carry_regime"),
            ("Sizing", "sizing_factor"),
        ],
        ArtifactType.REGIME_JOURNAL: [
            ("As Of", "as_of"),
            ("Summary", "summary"),
            ("Key Changes", "key_changes"),
            ("Risks", "risks"),
        ],
        ArtifactType.TEST_SPEC: [
            ("Budget", "search_budget"),
            ("Datasets", "datasets"),
            ("Metrics", "eval_metrics"),
            ("Frozen", "frozen_at"),
        ],
        ArtifactType.EXPERIMENT_REPORT: [
            ("Variants", "variants_tested"),
            ("Net Sharpe", "net_metrics.sharpe"),
            ("Profit Factor", "net_metrics.profit_factor"),
            ("Caveats", "implementation_caveats"),
        ],
        ArtifactType.TRADE_SHEET: [
            ("Holding", "holding_period_target"),
            ("Instruments", "instruments"),
            ("Entry Rules", "entry_rules"),
            ("Kill Criteria", "kill_criteria"),
        ],
        ArtifactType.PILOT_DECISION: [
            ("Decision", "operator_decision"),
            ("Approved", "approved"),
            ("Trade Sheet", "trade_sheet_ref"),
            ("By", "decided_by"),
            ("Notes", "operator_notes"),
        ],
        ArtifactType.REBALANCE_SHEET: [
            ("Approval", "approval_status"),
            ("Decision", "decision_source"),
            ("By", "decided_by"),
            ("Cost", "estimated_cost"),
            ("Notes", "operator_notes"),
            ("Targets", "target_positions"),
            ("Deltas", "deltas"),
        ],
        ArtifactType.ENGINE_A_SIGNAL_SET: [
            ("As Of", "as_of"),
            ("Signals", "signals"),
            ("Forecasts", "combined_forecast"),
            ("Regime Ref", "regime_ref"),
        ],
        ArtifactType.EXECUTION_REPORT: [
            ("Trades Submitted", "trades_submitted"),
            ("Trades Filled", "trades_filled"),
            ("Venue", "venue"),
            ("Cost", "cost"),
        ],
        ArtifactType.POST_MORTEM_NOTE: [
            ("Thesis", "thesis_assessment"),
            ("Worked", "what_worked"),
            ("Failed", "what_failed"),
            ("Lessons", "lessons"),
        ],
    }.get(artifact_type, [])


def _artifact_value_from_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _format_artifact_summary_value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        preview = ", ".join(str(item) for item in value[:4])
        if len(value) > 4:
            preview = f"{preview}, +{len(value) - 4} more"
        return preview
    if isinstance(value, dict):
        keys = list(value.keys())
        if not keys:
            return ""
        preview = ", ".join(str(key) for key in keys[:4])
        if len(keys) > 4:
            preview = f"{preview}, +{len(keys) - 4} more"
        return preview
    if isinstance(value, float):
        return f"{value:.3f}"
    text = str(value)
    return text if len(text) <= 160 else f"{text[:157]}..."


def _serialize_research_artifact(envelope: ArtifactEnvelope) -> dict[str, Any]:
    body = envelope.body if isinstance(envelope.body, dict) else {}
    summary = []
    for label, path in _artifact_body_summary_fields(envelope.artifact_type):
        value = _format_artifact_summary_value(_artifact_value_from_path(body, path))
        if value:
            summary.append({"label": label, "value": value})
    return {
        "artifact_id": envelope.artifact_id,
        "artifact_type": envelope.artifact_type.value,
        "artifact_label": envelope.artifact_type.value.replace("_", " ").title(),
        "chain_id": envelope.chain_id,
        "version": envelope.version,
        "parent_id": envelope.parent_id,
        "engine": envelope.engine.value,
        "ticker": envelope.ticker or "",
        "edge_family": envelope.edge_family.value if envelope.edge_family else "",
        "status": envelope.status.value,
        "created_at": envelope.created_at or "",
        "created_by": envelope.created_by,
        "tags": list(envelope.tags or []),
        "summary": summary,
        "body": body,
    }


def _build_research_artifact_chain_context(
    chain_id: str,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    store = artifact_store or ArtifactStore()
    chain = store.get_chain(chain_id)
    artifacts = [_serialize_research_artifact(envelope) for envelope in chain]
    for index, artifact in enumerate(artifacts, start=1):
        artifact["dom_id"] = f"chain-artifact-v{int(artifact.get('version') or index)}"
        artifact["sequence_label"] = f"Step {index}"
    latest = artifacts[-1] if artifacts else None
    now = datetime.now(timezone.utc)
    latest_scoring = next(
        (
            item
            for item in reversed(artifacts)
            if item.get("artifact_type") == ArtifactType.SCORING_RESULT.value
        ),
        None,
    )
    latest_trade_sheet = next(
        (
            item
            for item in reversed(artifacts)
            if item.get("artifact_type") == ArtifactType.TRADE_SHEET.value
        ),
        None,
    )
    latest_pilot_decision = next(
        (
            item
            for item in reversed(artifacts)
            if item.get("artifact_type") == ArtifactType.PILOT_DECISION.value
        ),
        None,
    )
    latest_review_trigger = next(
        (
            item
            for item in reversed(artifacts)
            if item.get("artifact_type") == ArtifactType.REVIEW_TRIGGER.value
        ),
        None,
    )
    latest_rebalance_sheet = next(
        (
            item
            for item in reversed(artifacts)
            if item.get("artifact_type") == ArtifactType.REBALANCE_SHEET.value
        ),
        None,
    )
    pilot_signoff_required = bool(
        latest_scoring
        and latest_trade_sheet
        and str((latest_scoring.get("body") or {}).get("next_stage") or "").strip().lower() == "pilot"
    )
    review_ack_pending = bool(
        latest_review_trigger
        and not bool((latest_review_trigger.get("body") or {}).get("operator_ack"))
    )
    review_recommended_action = (
        str((latest_review_trigger.get("body") or {}).get("recommended_action") or "").strip().lower()
        if latest_review_trigger
        else ""
    )
    review_context = None
    if latest_review_trigger:
        review_body = latest_review_trigger.get("body") or {}
        review_flags = [
            str(flag).strip()
            for flag in (review_body.get("flags") or [])
            if str(flag).strip()
        ]
        review_context = {
            "trigger_source": str(review_body.get("trigger_source") or "").strip(),
            "health_status": str(review_body.get("health_status") or "").strip(),
            "recommended_action": review_recommended_action,
            "flags": review_flags[:4],
            "flag_count": len(review_flags),
            "operator_ack": bool(review_body.get("operator_ack")),
            "operator_notes": str(review_body.get("operator_notes") or "").strip(),
        }
    rebalance_executed = False
    rebalance_can_execute = False
    rebalance_can_dismiss = False
    rebalance_move_count = 0
    rebalance_context = None
    if latest_rebalance_sheet:
        rebalance_version = int(latest_rebalance_sheet.get("version") or 0)
        rebalance_body = latest_rebalance_sheet.get("body") or {}
        deltas = dict(rebalance_body.get("deltas") or {})
        ranked_moves = sorted(
            (
                {
                    "instrument": str(instrument),
                    "delta": float(delta or 0.0),
                }
                for instrument, delta in deltas.items()
                if abs(float(delta or 0.0)) > 0.0
            ),
            key=lambda item: abs(item["delta"]),
            reverse=True,
        )
        rebalance_move_count = len(ranked_moves)
        rebalance_executed = any(
            envelope.artifact_type == ArtifactType.EXECUTION_REPORT and int(envelope.version or 0) > rebalance_version
            for envelope in chain
        )
        rebalance_can_execute = rebalance_move_count > 0 and not rebalance_executed
        rebalance_can_dismiss = not rebalance_executed
        rebalance_context = {
            "approval_status": str(rebalance_body.get("approval_status") or "").strip(),
            "decision_source": str(rebalance_body.get("decision_source") or "").strip(),
            "decided_by": str(rebalance_body.get("decided_by") or "").strip(),
            "operator_notes": str(rebalance_body.get("operator_notes") or "").strip(),
            "estimated_cost": float(rebalance_body.get("estimated_cost") or 0.0),
            "move_count": rebalance_move_count,
            "top_moves": ranked_moves[:3],
        }
    latest_type = str((latest or {}).get("artifact_type") or "").strip().lower()
    next_lane = (
        str((latest_scoring.get("body") or {}).get("next_stage") or "").strip().lower()
        if latest_scoring
        else ("pilot_ready" if latest_type == ArtifactType.TRADE_SHEET.value else "")
    )
    if latest_type == ArtifactType.REBALANCE_SHEET.value:
        next_lane = "rebalance_decision"
    if latest_type == ArtifactType.REVIEW_TRIGGER.value:
        next_lane = "review_pending"
    if pilot_signoff_required and not next_lane:
        next_lane = "pilot_ready"

    next_operator_move = "inspect chain and synthesize"
    posture_title = "Chain in progress"
    posture_detail = "Inspect the latest artifact and decide the next operator action."
    posture_tone = "neutral"
    if latest_type == ArtifactType.REVIEW_TRIGGER.value:
        posture_title = "Review acknowledgement pending"
        posture_detail = "A decay or kill review is waiting for an operator decision."
        posture_tone = "warning"
        next_operator_move = "acknowledge review"
    elif latest_type == ArtifactType.REBALANCE_SHEET.value:
        posture_title = "Rebalance decision pending"
        posture_detail = "Engine A produced a rebalance proposal; execute it or dismiss it from the workbench."
        posture_tone = "warning"
        next_operator_move = "execute or dismiss rebalance"
    elif pilot_signoff_required and latest_pilot_decision is None:
        posture_title = "Pilot sign-off pending"
        posture_detail = "The trade plan is ready and needs an approve/reject call before the chain can advance."
        posture_tone = "warning"
        next_operator_move = "approve or reject pilot"
    elif latest_type == ArtifactType.SCORING_RESULT.value:
        posture_title = "Ready for synthesis"
        posture_detail = "Score is recorded; summarize the chain and decide whether it advances, parks, or dies."
        posture_tone = "positive" if str((latest.get("body") or {}).get("outcome") or "").strip().lower() == "promote" else "neutral"
        next_operator_move = "inspect score and synthesize"
    elif latest_type == ArtifactType.PILOT_DECISION.value:
        decision = str((latest.get("body") or {}).get("operator_decision") or "").strip().lower()
        posture_title = "Pilot decision recorded"
        posture_detail = "Pilot sign-off is in the chain record. Review follow-through or capture the post-mortem."
        posture_tone = "positive" if decision == "approve" else "negative"
        next_operator_move = "review follow-through"
    elif latest_type == ArtifactType.POST_MORTEM_NOTE.value:
        posture_title = "Closed with post-mortem"
        posture_detail = "The learning loop is captured; use archive search to compare similar cases later."
        posture_tone = "closed"
        next_operator_move = "archive and compare"
    elif latest_type == ArtifactType.RETIREMENT_MEMO.value:
        posture_title = "Retired"
        posture_detail = "The chain has been retired. Use the memo and archive history as the reference state."
        posture_tone = "negative"
        next_operator_move = "review retirement context"
    elif latest_type == ArtifactType.TRADE_SHEET.value:
        posture_title = "Trade sheet ready"
        posture_detail = "The chain has a trade expression. Confirm whether it is ready for pilot or needs more review."
        posture_tone = "positive"
        next_operator_move = "review trade expression"

    lifecycle = _build_research_chain_lifecycle(chain) if chain else {
        "milestones": [],
        "completed_count": 0,
        "total_count": len(_RESEARCH_CHAIN_LIFECYCLE_STAGES),
        "summary": "",
    }
    latest_artifact_id = str((latest or {}).get("artifact_id") or "")
    artifact_navigation = []
    for artifact in artifacts:
        is_latest_artifact = str(artifact.get("artifact_id") or "") == latest_artifact_id
        artifact["is_latest"] = is_latest_artifact
        artifact_navigation.append(
            {
                "artifact_id": str(artifact.get("artifact_id") or ""),
                "dom_id": str(artifact.get("dom_id") or ""),
                "label": str(artifact.get("artifact_label") or "Artifact"),
                "version": int(artifact.get("version") or 0),
                "engine": str(artifact.get("engine") or ""),
                "created_label": _relative_time_label(artifact.get("created_at"), now=now),
                "is_latest": is_latest_artifact,
            }
        )
    first_created_at = artifacts[0].get("created_at") if artifacts else ""
    latest_created_at = latest.get("created_at") if latest else ""
    return {
        "chain_id": chain_id,
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "latest": latest,
        "latest_scoring": latest_scoring,
        "pilot_decision": latest_pilot_decision,
        "latest_review_trigger": latest_review_trigger,
        "latest_rebalance_sheet": latest_rebalance_sheet,
        "pilot_signoff_required": pilot_signoff_required,
        "pilot_signoff_pending": pilot_signoff_required and latest_pilot_decision is None,
        "review_ack_pending": review_ack_pending,
        "review_recommended_action": review_recommended_action,
        "review_context": review_context,
        "rebalance_executed": rebalance_executed,
        "rebalance_can_execute": rebalance_can_execute,
        "rebalance_can_dismiss": rebalance_can_dismiss,
        "rebalance_move_count": rebalance_move_count,
        "rebalance_context": rebalance_context,
        "can_generate_post_mortem": any(
            envelope.artifact_type == ArtifactType.HYPOTHESIS_CARD for envelope in chain
        ),
        "post_mortem_count": sum(
            1 for envelope in chain if envelope.artifact_type == ArtifactType.POST_MORTEM_NOTE
        ),
        "lifecycle": lifecycle,
        "artifact_navigation": artifact_navigation,
        "operator_posture_title": posture_title,
        "operator_posture_detail": posture_detail,
        "operator_posture_tone": posture_tone,
        "next_lane": next_lane or "review",
        "next_operator_move": next_operator_move,
        "first_created_at": first_created_at,
        "first_created_label": _relative_time_label(first_created_at, now=now),
        "latest_created_at": latest_created_at,
        "latest_created_label": _relative_time_label(latest_created_at, now=now),
        "error": "" if artifacts else f"No research artifacts found for chain {chain_id[:8]}.",
        "generated_at": _utc_now_iso(),
    }


def _build_research_artifact_detail(
    artifact_id: str,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any] | None:
    store = artifact_store or ArtifactStore()
    artifact = store.get(artifact_id)
    if artifact is None:
        return None
    return _serialize_research_artifact(artifact)


def _find_chain_artifact(
    chain_id: str,
    artifact_type: ArtifactType,
    *,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactEnvelope | None:
    return _manual_find_chain_artifact(
        chain_id,
        artifact_type,
        artifact_store=artifact_store,
    )


def _serialize_research_synthesis_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event.get("payload") or {})
    descriptor = dict(event.get("provenance_descriptor") or {})
    return {
        "event_id": event.get("event_id") or event.get("id") or "",
        "chain_id": payload.get("chain_id") or event.get("source_ref") or descriptor.get("chain_id") or "",
        "ticker": payload.get("ticker") or event.get("symbol") or "",
        "artifact_count": int(payload.get("artifact_count") or descriptor.get("artifact_count") or 0),
        "latest_artifact_id": payload.get("latest_artifact_id") or descriptor.get("latest_artifact_id") or "",
        "latest_artifact_type": payload.get("latest_artifact_type") or "",
        "summary": str(payload.get("summary") or event.get("detail") or "").strip(),
        "created_at": str(event.get("event_timestamp") or event.get("retrieved_at") or ""),
    }


_RESEARCH_CHAIN_LIFECYCLE_STAGES: tuple[tuple[str, str, tuple[ArtifactType, ...]], ...] = (
    ("event", "Event", (ArtifactType.EVENT_CARD,)),
    ("hypothesis", "Hypothesis", (ArtifactType.HYPOTHESIS_CARD,)),
    ("challenge", "Challenge", (ArtifactType.FALSIFICATION_MEMO,)),
    ("test_spec", "Test Spec", (ArtifactType.TEST_SPEC,)),
    ("experiment", "Experiment", (ArtifactType.EXPERIMENT_REPORT,)),
    ("trade", "Trade", (ArtifactType.TRADE_SHEET,)),
    ("pilot_decision", "Pilot Sign-Off", (ArtifactType.PILOT_DECISION,)),
    ("score", "Score", (ArtifactType.SCORING_RESULT,)),
    ("review", "Review", (ArtifactType.REVIEW_TRIGGER,)),
    ("post_mortem", "Post-Mortem", (ArtifactType.POST_MORTEM_NOTE,)),
    ("retirement", "Retirement", (ArtifactType.RETIREMENT_MEMO,)),
)


def _extract_research_artifact_note(envelope: ArtifactEnvelope) -> str:
    serialized = _serialize_research_artifact(envelope)
    summary = serialized.get("summary") if isinstance(serialized.get("summary"), list) else []
    for item in summary:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if value:
            return value
    body = envelope.body if isinstance(envelope.body, dict) else {}
    for key in ("summary", "thesis_assessment", "trigger_detail", "outcome_reason", "diagnosis", "mechanism"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _build_research_chain_lifecycle(chain: list[ArtifactEnvelope]) -> dict[str, Any]:
    counts: dict[ArtifactType, int] = {}
    for envelope in chain:
        counts[envelope.artifact_type] = counts.get(envelope.artifact_type, 0) + 1

    milestones: list[dict[str, Any]] = []
    summary_parts: list[str] = []
    completed_count = 0
    for key, label, artifact_types in _RESEARCH_CHAIN_LIFECYCLE_STAGES:
        count = sum(counts.get(artifact_type, 0) for artifact_type in artifact_types)
        present = count > 0
        if present:
            completed_count += 1
            summary_parts.append(f"{label} x{count}" if count > 1 else label)
        milestones.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "present": present,
            }
        )

    summary = " -> ".join(summary_parts[:6])
    if len(summary_parts) > 6:
        suffix = f"+{len(summary_parts) - 6} more"
        summary = f"{summary} -> {suffix}" if summary else suffix

    return {
        "milestones": milestones,
        "completed_count": completed_count,
        "total_count": len(_RESEARCH_CHAIN_LIFECYCLE_STAGES),
        "summary": summary,
    }
