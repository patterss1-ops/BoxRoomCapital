"""Shared research operator and manual execution helpers."""
from __future__ import annotations

from typing import Any, Callable

from data.pg_connection import get_pg_connection, release_pg_connection
from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, RetirementMemo
from research.manual_execution import (
    build_manual_engine_a_execution_report as _manual_execution_build_engine_a_execution_report,
    build_manual_engine_a_trade_instruments as _manual_execution_build_engine_a_trade_instruments,
    build_manual_engine_a_trade_sheet as _manual_execution_build_engine_a_trade_sheet,
    find_chain_artifact as _manual_execution_find_chain_artifact,
    latest_artifact_by_type as _manual_execution_latest_artifact_by_type,
    manual_engine_a_broker_target as _manual_execution_broker_target,
    parse_contract_details as _manual_execution_parse_contract_details,
    queue_manual_engine_a_order_intents as _manual_execution_queue_manual_engine_a_order_intents,
    supersede_rebalance_sheet as _manual_execution_supersede_rebalance_sheet,
)


def _update_research_pipeline_state(
    chain_id: str,
    stage: str,
    *,
    outcome: str,
    operator_ack: bool = True,
    operator_notes: str = "",
) -> None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE research.pipeline_state
                SET current_stage = %s,
                    outcome = %s,
                    operator_ack = %s,
                    operator_notes = %s,
                    updated_at = now()
                WHERE chain_id = %s
                """,
                (
                    stage,
                    outcome,
                    operator_ack,
                    operator_notes or None,
                    chain_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def _operator_created_by(actor: str) -> str:
    clean_actor = str(actor or "").strip() or "operator"
    return clean_actor if clean_actor.startswith("operator:") else f"operator:{clean_actor}"


def _render_research_operator_output(
    request: Any,
    *,
    templates: Any,
    build_research_operator_output_context: Callable[..., dict[str, Any]],
    chain_id: str = "",
    queue_lane: str = "all",
    active_view: str = "all",
    synthesis: dict[str, Any] | None = None,
    operator_action: dict[str, Any] | None = None,
    pilot_decision: dict[str, Any] | None = None,
    post_mortem: dict[str, Any] | None = None,
    queued_intake: dict[str, Any] | None = None,
    error: str = "",
) -> Any:
    return templates.TemplateResponse(
        request,
        "_research_operator_output.html",
        {
            "request": request,
            **build_research_operator_output_context(
                chain_id=chain_id,
                queue_lane=queue_lane,
                active_view=active_view,
                synthesis=synthesis,
                operator_action=operator_action,
                pilot_decision=pilot_decision,
                post_mortem=post_mortem,
                queued_intake=queued_intake,
                error=error,
            ),
        },
    )


def _build_operator_action_payload(
    *,
    chain_id: str,
    title: str,
    status: str,
    summary: str,
    artifacts: list[ArtifactEnvelope] | None = None,
    serialize_research_artifact: Callable[[ArtifactEnvelope], dict[str, Any]],
) -> dict[str, Any]:
    serialized = [serialize_research_artifact(artifact) for artifact in (artifacts or [])]
    ticker = ""
    for item in serialized:
        ticker = str(item.get("ticker") or "").strip()
        if ticker:
            break
    return {
        "chain_id": chain_id,
        "title": title,
        "status": status,
        "summary": summary,
        "ticker": ticker,
        "artifacts": serialized,
        "artifact_count": len(serialized),
    }


def _find_chain_artifact(
    chain_id: str,
    artifact_type: ArtifactType,
    *,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactEnvelope | None:
    return _manual_execution_find_chain_artifact(
        chain_id,
        artifact_type,
        artifact_store=artifact_store,
    )


def _latest_artifact_by_type(
    artifact_type: ArtifactType,
    *,
    engine: Engine,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactEnvelope | None:
    return _manual_execution_latest_artifact_by_type(
        artifact_type,
        engine=engine,
        artifact_store=artifact_store,
    )


def _supersede_rebalance_sheet(
    *,
    rebalance: ArtifactEnvelope,
    approval_status: str,
    actor: str,
    notes: str,
    artifact_store: ArtifactStore,
) -> ArtifactEnvelope:
    return _manual_execution_supersede_rebalance_sheet(
        rebalance=rebalance,
        approval_status=approval_status,
        actor=actor,
        notes=notes,
        artifact_store=artifact_store,
    )


def _manual_engine_a_broker_target() -> str:
    return _manual_execution_broker_target()


def _parse_contract_details(contract_details: str | None) -> dict[str, str]:
    return _manual_execution_parse_contract_details(contract_details)


def _build_manual_engine_a_trade_instruments(
    deltas: dict[str, float],
    *,
    size_mode: str = "auto",
    ig_market_details: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str, list[Any]]:
    return _manual_execution_build_engine_a_trade_instruments(
        deltas,
        size_mode=size_mode,
        ig_market_details=ig_market_details,
    )


def _build_manual_engine_a_trade_sheet(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    actor: str,
    artifact_store: ArtifactStore,
    size_mode: str = "auto",
    ig_market_details: dict[str, dict[str, Any]] | None = None,
    symbols: list[str] | None = None,
) -> ArtifactEnvelope:
    return _manual_execution_build_engine_a_trade_sheet(
        chain_id=chain_id,
        rebalance=rebalance,
        actor=actor,
        artifact_store=artifact_store,
        size_mode=size_mode,
        ig_market_details=ig_market_details,
        symbols=symbols,
    )


def _queue_manual_engine_a_order_intents(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    trade_sheet: ArtifactEnvelope,
    actor: str,
    order_intent_creator: Callable[..., dict[str, Any]],
    db_path: str,
) -> list[dict[str, Any]]:
    return _manual_execution_queue_manual_engine_a_order_intents(
        chain_id=chain_id,
        rebalance=rebalance,
        trade_sheet=trade_sheet,
        actor=actor,
        order_intent_creator=order_intent_creator,
        db_path=db_path,
    )


def _build_manual_engine_a_execution_report(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    actor: str,
    artifact_store: ArtifactStore,
    queued_intents: list[dict[str, Any]],
) -> ArtifactEnvelope:
    return _manual_execution_build_engine_a_execution_report(
        chain_id=chain_id,
        rebalance=rebalance,
        actor=actor,
        artifact_store=artifact_store,
        queued_intents=queued_intents,
    )


def _build_review_retirement_memo(
    *,
    review: ArtifactEnvelope,
    actor: str,
    notes: str,
    artifact_store: ArtifactStore,
) -> ArtifactEnvelope:
    review_body = dict(review.body)
    strategy_id = str(review_body.get("strategy_id") or review.ticker or "").strip() or "unknown"
    trigger_detail = str(notes or "").strip() or "Operator confirmed kill from research dashboard."
    memo = RetirementMemo(
        hypothesis_ref=strategy_id,
        trigger="operator_decision",
        trigger_detail=trigger_detail,
        diagnosis=f"Operator Decision triggered: {trigger_detail}",
        lessons=["Document the decisive evidence before reconsidering reactivation."],
        final_status="dead",
        performance_summary=None,
        live_duration_days=None,
    )
    envelope = ArtifactEnvelope(
        artifact_type=ArtifactType.RETIREMENT_MEMO,
        engine=review.engine,
        ticker=review.ticker,
        edge_family=review.edge_family,
        chain_id=review.chain_id,
        parent_id=review.artifact_id,
        body=memo,
        created_by=_operator_created_by(actor),
        tags=["retirement", "operator_decision"],
    )
    envelope.artifact_id = artifact_store.save(envelope)
    return envelope
