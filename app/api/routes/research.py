"""Research, strategy, engine control, ideas, and brief routes."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config
from app.api import job_helpers as _job_helpers
from app.api import operator_surface_helpers as _operator_surface_helpers
from app.api import research_operator_helpers as _research_operator_helpers
from app.api.shared import (
    TEMPLATES,
    control,
    research,
    research_dashboard,
    logger as shared_logger,
    _utc_now_iso,
    _get_cached_value,
    _invalidate_cached_values,
    _invalidate_research_cached_values,
    action_message,
    _LATEST_BRIEFS,
    _BRIEF_LOCK,
    _RISK_BRIEFING_CACHE_TTL_SECONDS,
    _PORTFOLIO_ANALYTICS_CACHE_TTL_SECONDS,
    _RESEARCH_CACHE_TTL_SECONDS,
)
from data.trade_db import (
    DB_PATH,
    complete_calibration_run,
    create_job,
    create_calibration_run,
    create_strategy_parameter_set,
    get_active_strategy_parameter_set,
    get_calibration_run,
    get_calibration_points,
    get_calibration_runs,
    get_fund_daily_reports,
    get_job,
    get_jobs,
    get_strategy_parameter_sets,
    get_strategy_parameter_set,
    get_strategy_promotions,
    insert_calibration_points,
    promote_strategy_parameter_set,
    update_job,
    get_trade_idea,
    get_trade_ideas,
    get_idea_transitions,
)
from fund.promotion_gate import build_promotion_gate_report, validate_lane_transition
from fund.promotion_gate import PromotionGateConfig, evaluate_promotion_gate
from fund.execution_quality import get_execution_quality_payload
from app.engine.signal_shadow import get_signal_shadow_report, run_signal_shadow_cycle
from intelligence.jobs.signal_layer_jobs import (
    build_ranked_candidates,
    enrich_signal_shadow_payload,
    run_tier1_shadow_jobs,
    summarize_research_overlay,
)
from intelligence.market_brief import generate_brief
from intelligence.event_store import EventRecord, EventStore
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    PromotionOutcome,
)
from research.model_router import ModelRouter
from research.shared.decay_review import DecayReviewService
from research.shared.pilot_signoff import PilotSignoffService
from research.shared.post_mortem import PostMortemService
from research.shared.synthesis import SynthesisService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["research"])


# ---------------------------------------------------------------------------
# Standalone version of _execute_control_action (was a closure in create_app)
# ---------------------------------------------------------------------------

def _execute_control_action(job_type: str, fn: Callable, label: str, **job_kwargs) -> str:
    """Run a control action with job tracking and return an HTML action message."""
    job_id = str(uuid.uuid4())
    create_job(job_id=job_id, job_type=job_type, status="running", **job_kwargs)
    try:
        result = fn()
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc))
        return action_message(f"{label} failed: {exc}", ok=False)
    if result["ok"]:
        update_job(job_id, status="completed", result=result["message"])
        return action_message(result["message"], ok=True)
    update_job(job_id, status="failed", error=result["message"])
    return action_message(result["message"], ok=False)


# ---------------------------------------------------------------------------
# Background job runners (module-level, used by action routes)
# ---------------------------------------------------------------------------

def _run_scan_job(job_id: str, mode: str):
    _job_helpers._run_scan_job(
        job_id,
        mode,
        update_job=update_job,
        scan_once=control.scan_once,
    )


def _run_reconcile_job(job_id: str):
    _job_helpers._run_reconcile_job(
        job_id,
        update_job=update_job,
        reconcile=control.reconcile,
    )


def _run_signal_shadow_job(job_id: str):
    _job_helpers._run_signal_shadow_job(
        job_id,
        update_job=update_job,
        run_signal_shadow_cycle=run_signal_shadow_cycle,
    )


def _run_signal_tier1_job(job_id: str):
    _job_helpers._run_signal_tier1_job(
        job_id,
        update_job=update_job,
        run_tier1_shadow_jobs=run_tier1_shadow_jobs,
    )


def _run_close_job(job_id: str, spread_id: str, ticker: str, reason: str):
    _job_helpers._run_close_job(
        job_id,
        spread_id,
        ticker,
        reason,
        update_job=update_job,
        close_spread=control.close_spread,
    )


def _run_discovery_job(job_id: str, mode: str, details: bool, strikes: str):
    _job_helpers._run_discovery_job(
        job_id,
        mode,
        details,
        strikes,
        update_job=update_job,
        run_discovery=research.run_discovery,
    )


def _run_calibration_job(job_id: str, index_filter: str, verbose: bool):
    _job_helpers._run_calibration_job(
        job_id,
        index_filter,
        verbose,
        create_calibration_run=create_calibration_run,
        update_job=update_job,
        run_calibration=research.run_calibration,
        insert_calibration_points=insert_calibration_points,
        complete_calibration_run=complete_calibration_run,
    )


# ---------------------------------------------------------------------------
# Helpers used only by research routes (copied from server.py module level)
# ---------------------------------------------------------------------------

def _parse_job_result(raw: str) -> Any:
    return _job_helpers._parse_job_result(raw)


def _summarize_top_candidates(rows: Any, limit: int = 3) -> list[dict[str, Any]]:
    return _job_helpers._summarize_top_candidates(rows, limit=limit)


def _build_signal_shadow_job_summary(parsed_result: Any) -> dict[str, Any] | None:
    return _job_helpers._build_signal_shadow_job_summary(
        parsed_result,
        build_ranked_candidates=build_ranked_candidates,
        summarize_research_overlay=summarize_research_overlay,
    )


def _build_signal_tier1_job_summary(parsed_result: Any) -> dict[str, Any] | None:
    return _job_helpers._build_signal_tier1_job_summary(
        parsed_result,
        build_ranked_candidates=build_ranked_candidates,
        summarize_research_overlay=summarize_research_overlay,
    )


def _build_job_detail_summary(job_type: str, parsed_result: Any) -> dict[str, Any] | None:
    return _job_helpers._build_job_detail_summary(
        job_type,
        parsed_result,
        build_ranked_candidates=build_ranked_candidates,
        summarize_research_overlay=summarize_research_overlay,
    )


def _unavailable_risk_briefing_payload(
    message: str,
    action: str,
    code: str = "RISK_DATA_UNAVAILABLE",
) -> dict[str, Any]:
    return _operator_surface_helpers._unavailable_risk_briefing_payload(
        message,
        action,
        code,
    )


def build_risk_briefing_payload() -> dict[str, Any]:
    """Build risk briefing payload for operator surfaces from B-003 providers."""
    from fund.nav import calculate_fund_nav
    from risk.portfolio_risk import get_risk_briefing
    return _operator_surface_helpers.build_risk_briefing_payload(
        calculate_fund_nav=calculate_fund_nav,
        get_risk_briefing=get_risk_briefing,
        unavailable_risk_briefing_payload=_unavailable_risk_briefing_payload,
    )


def build_portfolio_analytics_payload(days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS) -> dict[str, Any]:
    """Build portfolio analytics payload from fund daily NAV history."""
    from analytics.portfolio_analytics import (
        compute_drawdowns,
        compute_metrics,
        compute_rolling_stats,
    )
    return _operator_surface_helpers.build_portfolio_analytics_payload(
        days,
        max_days=config.PORTFOLIO_ANALYTICS_MAX_DAYS,
        rolling_window_default=config.PORTFOLIO_ANALYTICS_ROLLING_WINDOW,
        risk_free_rate=config.PORTFOLIO_ANALYTICS_RISK_FREE_RATE,
        get_fund_daily_reports=get_fund_daily_reports,
        compute_metrics=compute_metrics,
        compute_drawdowns=compute_drawdowns,
        compute_rolling_stats=compute_rolling_stats,
    )


# ---------------------------------------------------------------------------
# Helpers that use lazy imports from server.py (shared across routers)
# ---------------------------------------------------------------------------

def _update_research_pipeline_state(
    chain_id: str,
    stage: str,
    *,
    outcome: str,
    operator_ack: bool = True,
    operator_notes: str = "",
) -> None:
    _research_operator_helpers._update_research_pipeline_state(
        chain_id,
        stage,
        outcome=outcome,
        operator_ack=operator_ack,
        operator_notes=operator_notes,
    )


def _operator_created_by(actor: str) -> str:
    return _research_operator_helpers._operator_created_by(actor)


def _normalize_research_queue_lane(lane: str) -> str:
    normalized = str(lane or "").strip().lower()
    return normalized if normalized in {"all", "review", "pilot", "rebalance", "retirements"} else "all"


def _normalize_research_active_view(view: str) -> str:
    normalized = str(view or "").strip().lower()
    return normalized if normalized in {"all", "focus", "operator", "flow", "stale"} else "all"


def _render_research_operator_output(
    request: Request,
    *,
    chain_id: str = "",
    queue_lane: str = "all",
    active_view: str = "all",
    synthesis: dict[str, Any] | None = None,
    operator_action: dict[str, Any] | None = None,
    pilot_decision: dict[str, Any] | None = None,
    post_mortem: dict[str, Any] | None = None,
    queued_intake: dict[str, Any] | None = None,
    error: str = "",
):
    from app.api.server import _build_research_operator_output_context
    return _research_operator_helpers._render_research_operator_output(
        request,
        templates=TEMPLATES,
        build_research_operator_output_context=_build_research_operator_output_context,
        chain_id=chain_id,
        queue_lane=queue_lane,
        active_view=active_view,
        synthesis=synthesis,
        operator_action=operator_action,
        pilot_decision=pilot_decision,
        post_mortem=post_mortem,
        queued_intake=queued_intake,
        error=error,
    )


def _build_operator_action_payload(
    *,
    chain_id: str,
    title: str,
    status: str,
    summary: str,
    artifacts: list[ArtifactEnvelope] | None = None,
) -> dict[str, Any]:
    from app.api.server import _serialize_research_artifact
    return _research_operator_helpers._build_operator_action_payload(
        chain_id=chain_id,
        title=title,
        status=status,
        summary=summary,
        artifacts=artifacts,
        serialize_research_artifact=_serialize_research_artifact,
    )


def _find_chain_artifact(
    chain_id: str,
    artifact_type: ArtifactType,
    *,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactEnvelope | None:
    return _research_operator_helpers._find_chain_artifact(
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
    return _research_operator_helpers._latest_artifact_by_type(
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
    return _research_operator_helpers._supersede_rebalance_sheet(
        rebalance=rebalance,
        approval_status=approval_status,
        actor=actor,
        notes=notes,
        artifact_store=artifact_store,
    )


def _build_manual_engine_a_trade_instruments(
    deltas: dict[str, float],
    *,
    size_mode: str = "auto",
    ig_market_details: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str, list]:
    return _research_operator_helpers._build_manual_engine_a_trade_instruments(
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
    return _research_operator_helpers._build_manual_engine_a_trade_sheet(
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
) -> list[dict[str, Any]]:
    return _research_operator_helpers._queue_manual_engine_a_order_intents(
        chain_id=chain_id,
        rebalance=rebalance,
        trade_sheet=trade_sheet,
        actor=actor,
        order_intent_creator=create_order_intent_envelope,
        db_path=DB_PATH,
    )


def _build_manual_engine_a_execution_report(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    actor: str,
    artifact_store: ArtifactStore,
    queued_intents: list[dict[str, Any]],
) -> ArtifactEnvelope:
    return _research_operator_helpers._build_manual_engine_a_execution_report(
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
    return _research_operator_helpers._build_review_retirement_memo(
        review=review,
        actor=actor,
        notes=notes,
        artifact_store=artifact_store,
    )


def _queue_engine_b_intake(
    *,
    raw_content: str,
    source_class: str,
    source_ids: list[str],
    detail: str,
    job_type: str = "engine_b_intake",
    source_credibility: float | None = None,
    allow_ad_hoc: bool = True,
) -> dict[str, Any]:
    from app.api.server import _queue_engine_b_intake as _server_queue_engine_b_intake
    return _server_queue_engine_b_intake(
        raw_content=raw_content,
        source_class=source_class,
        source_ids=source_ids,
        detail=detail,
        job_type=job_type,
        source_credibility=source_credibility,
        allow_ad_hoc=allow_ad_hoc,
    )


# ===========================================================================
# Engine control action routes (POST /api/actions/...)
# ===========================================================================

@router.post("/api/actions/start", response_class=HTMLResponse)
def start_bot(mode: str = Form(default=config.TRADING_MODE)):
    return _execute_control_action("start_bot", lambda: control.start(mode=mode), "Start", mode=mode)


@router.post("/api/actions/stop", response_class=HTMLResponse)
def stop_bot():
    return _execute_control_action("stop_bot", control.stop, "Stop")


@router.post("/api/actions/pause", response_class=HTMLResponse)
def pause_bot():
    return _execute_control_action("pause_bot", control.pause, "Pause")


@router.post("/api/actions/resume", response_class=HTMLResponse)
def resume_bot():
    return _execute_control_action("resume_bot", control.resume, "Resume")


@router.post("/api/actions/scan-now", response_class=HTMLResponse)
def scan_now(mode: str = Form(default=config.TRADING_MODE)):
    job_id = str(uuid.uuid4())
    create_job(
        job_id=job_id,
        job_type="scan_once",
        status="queued",
        mode=mode,
        detail="Queued one-shot scan",
    )

    thread = threading.Thread(target=_run_scan_job, args=(job_id, mode), daemon=True)
    thread.start()
    return action_message(f"Queued one-shot scan job {job_id[:8]} ({mode.upper()}).", ok=True)


@router.post("/api/actions/reconcile", response_class=HTMLResponse)
def reconcile_now():
    job_id = str(uuid.uuid4())
    create_job(
        job_id=job_id,
        job_type="reconcile",
        status="queued",
        detail="Queued reconcile",
    )
    thread = threading.Thread(target=_run_reconcile_job, args=(job_id,), daemon=True)
    thread.start()
    return action_message(f"Queued reconcile job {job_id[:8]}.", ok=True)


@router.post("/api/actions/signal-shadow-run", response_class=HTMLResponse)
def signal_shadow_run():
    job_id = str(uuid.uuid4())
    create_job(
        job_id=job_id,
        job_type="signal_shadow_run",
        status="queued",
        detail="Queued signal shadow cycle",
    )
    thread = threading.Thread(target=_run_signal_shadow_job, args=(job_id,), daemon=True)
    thread.start()
    return action_message(f"Queued signal shadow run {job_id[:8]}.", ok=True)


@router.post("/api/actions/signal-tier1-run", response_class=HTMLResponse)
def signal_tier1_run():
    job_id = str(uuid.uuid4())
    create_job(
        job_id=job_id,
        job_type="signal_tier1_shadow_run",
        status="queued",
        detail="Queued tier-1 signal jobs + shadow ranking run",
    )
    thread = threading.Thread(target=_run_signal_tier1_job, args=(job_id,), daemon=True)
    thread.start()
    return action_message(f"Queued tier-1 shadow run {job_id[:8]}.", ok=True)


@router.post("/api/actions/close-spread", response_class=HTMLResponse)
def close_spread(
    spread_id: str = Form(default=""),
    ticker: str = Form(default=""),
    reason: str = Form(default="Manual close from control plane"),
):
    if not spread_id and not ticker:
        return action_message("Provide spread_id or ticker to close.", ok=False)

    job_id = str(uuid.uuid4())
    detail = f"Queued close spread_id={spread_id or '-'} ticker={ticker or '-'}"
    create_job(
        job_id=job_id,
        job_type="close_spread",
        status="queued",
        detail=detail,
    )
    thread = threading.Thread(
        target=_run_close_job,
        args=(job_id, spread_id.strip(), ticker.strip(), reason.strip()),
        daemon=True,
    )
    thread.start()
    return action_message(f"Queued close-spread job {job_id[:8]}.", ok=True)


@router.post("/api/actions/kill-switch-enable", response_class=HTMLResponse)
def kill_switch_enable(reason: str = Form(default="Manual operator kill switch")):
    return _execute_control_action(
        "kill_switch_enable",
        lambda: control.set_kill_switch(active=True, reason=reason, actor="operator"),
        "Kill switch enable", detail=reason,
    )


@router.post("/api/actions/kill-switch-disable", response_class=HTMLResponse)
def kill_switch_disable(reason: str = Form(default="Manual clear from control plane")):
    return _execute_control_action(
        "kill_switch_disable",
        lambda: control.set_kill_switch(active=False, reason=reason, actor="operator"),
        "Kill switch disable", detail=reason,
    )


@router.post("/api/actions/risk-throttle", response_class=HTMLResponse)
def risk_throttle(
    throttle_pct: float = Form(default=100.0),
    reason: str = Form(default="Manual risk throttle"),
):
    clamped = min(100.0, max(10.0, float(throttle_pct)))
    pct = clamped / 100.0
    detail = f"{clamped:.0f}% ({reason})"
    return _execute_control_action(
        "risk_throttle",
        lambda: control.set_risk_throttle(pct=pct, reason=reason, actor="operator"),
        "Risk throttle", detail=detail,
    )


@router.post("/api/actions/cooldown-set", response_class=HTMLResponse)
def cooldown_set(
    ticker: str = Form(default=""),
    minutes: int = Form(default=30),
    reason: str = Form(default="Manual market cooldown"),
):
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return action_message("Ticker is required for cooldown.", ok=False)
    duration = max(1, int(minutes))
    detail = f"{clean_ticker} {duration}m ({reason})"
    return _execute_control_action(
        "cooldown_set",
        lambda: control.set_market_cooldown(ticker=clean_ticker, minutes=duration, reason=reason, actor="operator"),
        "Cooldown set", detail=detail,
    )


@router.post("/api/actions/cooldown-clear", response_class=HTMLResponse)
def cooldown_clear(
    ticker: str = Form(default=""),
    reason: str = Form(default="Manual cooldown clear"),
):
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return action_message("Ticker is required to clear cooldown.", ok=False)
    detail = f"{clean_ticker} ({reason})"
    return _execute_control_action(
        "cooldown_clear",
        lambda: control.clear_market_cooldown(ticker=clean_ticker, reason=reason, actor="operator"),
        "Cooldown clear", detail=detail,
    )


# --- Pipeline control endpoints ---

@router.post("/api/actions/scheduler-start", response_class=HTMLResponse)
def scheduler_start_action():
    result = control.start_scheduler()
    ok = result.get("status") != "error"
    return action_message(f"Scheduler: {result['status']}", ok=ok)


@router.post("/api/actions/scheduler-stop", response_class=HTMLResponse)
def scheduler_stop_action():
    result = control.stop_scheduler()
    return action_message(f"Scheduler: {result['status']}", ok=True)


@router.post("/api/actions/dispatcher-start", response_class=HTMLResponse)
def dispatcher_start_action():
    result = control.start_dispatcher()
    ok = result.get("status") != "error"
    return action_message(f"Dispatcher: {result['status']}", ok=ok)


@router.post("/api/actions/dispatcher-stop", response_class=HTMLResponse)
def dispatcher_stop_action():
    result = control.stop_dispatcher()
    return action_message(f"Dispatcher: {result['status']}", ok=True)


@router.post("/api/actions/engine-a-start", response_class=HTMLResponse)
def engine_a_start_action():
    result = control.start_engine_a()
    ok = result.get("status") not in {"error", "disabled", "unavailable"}
    return action_message(f"Engine A: {result['status']}", ok=ok)


@router.post("/api/actions/engine-a-stop", response_class=HTMLResponse)
def engine_a_stop_action():
    result = control.stop_engine_a()
    ok = result.get("status") != "error"
    return action_message(f"Engine A: {result['status']}", ok=ok)


@router.post("/api/actions/engine-b-start", response_class=HTMLResponse)
def engine_b_start_action():
    result = control.start_engine_b()
    ok = result.get("status") not in {"error", "disabled", "unavailable"}
    return action_message(f"Engine B: {result['status']}", ok=ok)


@router.post("/api/actions/engine-b-stop", response_class=HTMLResponse)
def engine_b_stop_action():
    result = control.stop_engine_b()
    ok = result.get("status") != "error"
    return action_message(f"Engine B: {result['status']}", ok=ok)


# ===========================================================================
# Research action routes (POST /api/actions/research/...)
# ===========================================================================

@router.post("/api/actions/research/review-ack", response_class=HTMLResponse)
def research_review_ack_action(
    request: Request,
    chain_id: str = Form(default=""),
    decision: str = Form(default="park"),
    notes: str = Form(default="Acknowledged from research dashboard"),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip()
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    if not clean_chain_id:
        return render_output(
            error="Review chain_id is required.",
        )
    try:
        outcome = PromotionOutcome(decision.strip().lower())
    except ValueError:
        return render_output(
            chain_id=clean_chain_id,
            error="Decision must be promote, revise, park, or reject.",
        )

    try:
        store = ArtifactStore()
        service = DecayReviewService(artifact_store=store)
        acknowledged = service.acknowledge_review(
            chain_id=clean_chain_id,
            operator_decision=outcome,
            notes=notes.strip() or "Acknowledged from research dashboard",
        )
        stage = {
            PromotionOutcome.PROMOTE: "review_cleared",
            PromotionOutcome.REVISE: "review_revise",
            PromotionOutcome.PARK: "review_parked",
            PromotionOutcome.REJECT: "review_rejected",
        }[outcome]
        _update_research_pipeline_state(
            clean_chain_id,
            stage,
            outcome=outcome.value,
            operator_ack=True,
            operator_notes=notes.strip() or "Acknowledged from research dashboard",
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Review acknowledgement failed: {exc}",
        )

    _invalidate_research_cached_values()
    return render_output(
        chain_id=clean_chain_id,
        operator_action=_build_operator_action_payload(
            chain_id=clean_chain_id,
            title="Review Acknowledged",
            status=stage,
            summary=f"Review decision recorded as {outcome.value}.",
            artifacts=[acknowledged],
        ),
    )


@router.post("/api/actions/research/confirm-kill", response_class=HTMLResponse)
def research_confirm_kill_action(
    request: Request,
    chain_id: str = Form(default=""),
    actor: str = Form(default="operator"),
    notes: str = Form(default="Operator confirmed kill from research dashboard."),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else "operator"
    clean_notes = notes.strip() if isinstance(notes, str) else "Operator confirmed kill from research dashboard."
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    if not clean_chain_id:
        return render_output(
            error="Research chain_id is required to confirm a kill.",
        )

    store = ArtifactStore()
    review = _find_chain_artifact(clean_chain_id, ArtifactType.REVIEW_TRIGGER, artifact_store=store)
    if review is None:
        return render_output(
            chain_id=clean_chain_id,
            error=f"No review trigger found for chain {clean_chain_id[:8]}.",
        )
    if bool(dict(review.body).get("operator_ack")):
        return render_output(
            chain_id=clean_chain_id,
            error=f"Review chain {clean_chain_id[:8]} is already acknowledged.",
        )

    try:
        acknowledged = DecayReviewService(artifact_store=store).acknowledge_review(
            chain_id=clean_chain_id,
            operator_decision=PromotionOutcome.REJECT,
            notes=clean_notes or "Operator confirmed kill from research dashboard.",
        )
        _update_research_pipeline_state(
            clean_chain_id,
            "review_rejected",
            outcome=PromotionOutcome.REJECT.value,
            operator_ack=True,
            operator_notes=clean_notes or "Operator confirmed kill from research dashboard.",
        )
        retirement = _build_review_retirement_memo(
            review=acknowledged,
            actor=clean_actor or "operator",
            notes=clean_notes,
            artifact_store=store,
        )
        _update_research_pipeline_state(
            clean_chain_id,
            "retired",
            outcome=PromotionOutcome.REJECT.value,
            operator_ack=True,
            operator_notes=clean_notes or "Operator confirmed kill from research dashboard.",
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Kill confirmation failed: {exc}",
        )

    _invalidate_research_cached_values()
    return render_output(
        chain_id=clean_chain_id,
        operator_action=_build_operator_action_payload(
            chain_id=clean_chain_id,
            title="Kill Confirmed",
            status="retired",
            summary="Review rejected and retirement memo recorded.",
            artifacts=[acknowledged, retirement],
        ),
    )


@router.post("/api/actions/research/override-kill", response_class=HTMLResponse)
def research_override_kill_action(
    request: Request,
    chain_id: str = Form(default=""),
    actor: str = Form(default="operator"),
    notes: str = Form(default="Operator overrode kill recommendation."),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else "operator"
    clean_notes = notes.strip() if isinstance(notes, str) else "Operator overrode kill recommendation."
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    if not clean_chain_id:
        return render_output(
            error="Research chain_id is required to override a kill.",
        )

    store = ArtifactStore()
    review = _find_chain_artifact(clean_chain_id, ArtifactType.REVIEW_TRIGGER, artifact_store=store)
    if review is None:
        return render_output(
            chain_id=clean_chain_id,
            error=f"No review trigger found for chain {clean_chain_id[:8]}.",
        )
    if bool(dict(review.body).get("operator_ack")):
        return render_output(
            chain_id=clean_chain_id,
            error=f"Review chain {clean_chain_id[:8]} is already acknowledged.",
        )

    try:
        acknowledged = DecayReviewService(artifact_store=store).acknowledge_review(
            chain_id=clean_chain_id,
            operator_decision=PromotionOutcome.PROMOTE,
            notes=clean_notes or "Operator overrode kill recommendation.",
        )
        _update_research_pipeline_state(
            clean_chain_id,
            "review_cleared",
            outcome=PromotionOutcome.PROMOTE.value,
            operator_ack=True,
            operator_notes=clean_notes or "Operator overrode kill recommendation.",
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Kill override failed: {exc}",
        )

    _invalidate_research_cached_values()
    return render_output(
        chain_id=clean_chain_id,
        operator_action=_build_operator_action_payload(
            chain_id=clean_chain_id,
            title="Kill Override Saved",
            status="review_cleared",
            summary=f"Kill recommendation overridden by {clean_actor or 'operator'}.",
            artifacts=[acknowledged],
        ),
    )


@router.post("/api/actions/research/execute-rebalance", response_class=HTMLResponse)
def research_execute_rebalance_action(
    request: Request,
    chain_id: str = Form(default=""),
    actor: str = Form(default="operator"),
    notes: str = Form(default="Operator approved and executed Engine A rebalance."),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else "operator"
    clean_notes = notes.strip() if isinstance(notes, str) else "Operator approved and executed Engine A rebalance."
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    store = ArtifactStore()
    rebalance = (
        _find_chain_artifact(clean_chain_id, ArtifactType.REBALANCE_SHEET, artifact_store=store)
        if clean_chain_id
        else _latest_artifact_by_type(ArtifactType.REBALANCE_SHEET, engine=Engine.ENGINE_A, artifact_store=store)
    )
    if rebalance is None or not rebalance.chain_id:
        return render_output(
            chain_id=clean_chain_id,
            error="No Engine A rebalance proposal is available to execute.",
        )

    chain = store.get_chain(rebalance.chain_id)
    if any(
        envelope.artifact_type == ArtifactType.EXECUTION_REPORT and int(envelope.version or 0) > int(rebalance.version or 0)
        for envelope in chain
    ):
        return render_output(
            chain_id=rebalance.chain_id,
            error=f"Latest Engine A rebalance for chain {rebalance.chain_id[:8]} has already been executed.",
        )
    if not any(abs(float(delta or 0.0)) > 0.0 for delta in dict(rebalance.body).get("deltas", {}).values()):
        return render_output(
            chain_id=rebalance.chain_id,
            error=f"Rebalance chain {rebalance.chain_id[:8]} has no executable deltas.",
        )
    try:
        _build_manual_engine_a_trade_instruments(
            {
                instrument: float(delta)
                for instrument, delta in dict(rebalance.body).get("deltas", {}).items()
                if abs(float(delta or 0.0)) > 0.0
            }
        )
    except Exception as exc:
        return render_output(
            chain_id=rebalance.chain_id,
            error=f"Rebalance execution failed: {exc}",
        )

    try:
        approved_rebalance = _supersede_rebalance_sheet(
            rebalance=rebalance,
            approval_status="approved",
            actor=clean_actor or "operator",
            notes=clean_notes,
            artifact_store=store,
        )
        trade_sheet = _build_manual_engine_a_trade_sheet(
            chain_id=rebalance.chain_id,
            rebalance=approved_rebalance,
            actor=clean_actor or "operator",
            artifact_store=store,
        )
        queued_intents = _queue_manual_engine_a_order_intents(
            chain_id=rebalance.chain_id,
            rebalance=approved_rebalance,
            trade_sheet=trade_sheet,
            actor=clean_actor or "operator",
        )
        execution_report = _build_manual_engine_a_execution_report(
            chain_id=rebalance.chain_id,
            rebalance=approved_rebalance,
            actor=clean_actor or "operator",
            artifact_store=store,
            queued_intents=queued_intents,
        )
    except Exception as exc:
        return render_output(
            chain_id=rebalance.chain_id,
            error=f"Rebalance execution failed: {exc}",
        )

    _invalidate_research_cached_values()
    return render_output(
        chain_id=rebalance.chain_id,
        operator_action=_build_operator_action_payload(
            chain_id=rebalance.chain_id,
            title="Rebalance Executed",
            status="approved",
            summary="Engine A rebalance approved, tradesheet created, and order intents queued for dispatcher.",
            artifacts=[approved_rebalance, trade_sheet, execution_report],
        ),
    )


@router.post("/api/actions/research/dismiss-rebalance", response_class=HTMLResponse)
def research_dismiss_rebalance_action(
    request: Request,
    chain_id: str = Form(default=""),
    actor: str = Form(default="operator"),
    notes: str = Form(default="Operator dismissed Engine A rebalance."),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else "operator"
    clean_notes = notes.strip() if isinstance(notes, str) else "Operator dismissed Engine A rebalance."
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    store = ArtifactStore()
    rebalance = (
        _find_chain_artifact(clean_chain_id, ArtifactType.REBALANCE_SHEET, artifact_store=store)
        if clean_chain_id
        else _latest_artifact_by_type(ArtifactType.REBALANCE_SHEET, engine=Engine.ENGINE_A, artifact_store=store)
    )
    if rebalance is None or not rebalance.chain_id:
        return render_output(
            chain_id=clean_chain_id,
            error="No Engine A rebalance proposal is available to dismiss.",
        )

    chain = store.get_chain(rebalance.chain_id)
    if any(
        envelope.artifact_type == ArtifactType.EXECUTION_REPORT and int(envelope.version or 0) > int(rebalance.version or 0)
        for envelope in chain
    ):
        return render_output(
            chain_id=rebalance.chain_id,
            error=f"Latest Engine A rebalance for chain {rebalance.chain_id[:8]} has already been executed.",
        )

    try:
        blocked_rebalance = _supersede_rebalance_sheet(
            rebalance=rebalance,
            approval_status="blocked",
            actor=clean_actor or "operator",
            notes=clean_notes,
            artifact_store=store,
        )
    except Exception as exc:
        return render_output(
            chain_id=rebalance.chain_id,
            error=f"Rebalance dismissal failed: {exc}",
        )

    _invalidate_research_cached_values()
    return render_output(
        chain_id=rebalance.chain_id,
        operator_action=_build_operator_action_payload(
            chain_id=rebalance.chain_id,
            title="Rebalance Dismissed",
            status="blocked",
            summary="Engine A rebalance was blocked by operator decision.",
            artifacts=[blocked_rebalance],
        ),
    )


@router.post("/api/actions/research/engine-b-run", response_class=HTMLResponse)
def research_engine_b_run_action(
    request: Request,
    raw_content: str = Form(default=""),
    source_class: str = Form(default="news_wire"),
    source_credibility: float = Form(default=0.7),
    source_ids: str = Form(default=""),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    content = raw_content.strip()
    if not content:
        return render_output(
            error="Raw content is required.",
        )
    source_id_list = [item.strip() for item in source_ids.split(",") if item.strip()]
    if not source_id_list:
        source_id_list = [f"manual:{uuid.uuid4().hex[:8]}"]
    result = _queue_engine_b_intake(
        raw_content=content,
        source_class=source_class.strip() or "news_wire",
        source_credibility=max(0.0, min(1.0, float(source_credibility))),
        source_ids=source_id_list,
        detail=f"manual Engine B intake ({source_class}, ids={len(source_id_list)})",
        job_type="engine_b_manual",
        allow_ad_hoc=True,
    )
    if not result.get("ok"):
        return render_output(
            error=f"Engine B enqueue failed: {result.get('detail') or result.get('error', 'unknown')}.",
        )
    return render_output(
        queued_intake={
            "job_id": str(result["job_id"]),
            "source_class": source_class.strip() or "news_wire",
            "source_credibility": result.get("source_credibility"),
            "source_ids": source_id_list,
            "queue_depth": int(result.get("queue_depth") or 0),
            "content_preview": content[:220],
        },
    )


@router.post("/api/actions/research/synthesize", response_class=HTMLResponse)
def research_synthesize_action(
    request: Request,
    chain_id: str = Form(default=""),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    if not clean_chain_id:
        return render_output(
            error="Research chain_id is required for synthesis.",
        )

    store = ArtifactStore()
    try:
        from app.api.server import _build_research_artifact_chain_context
        chain_context = _build_research_artifact_chain_context(clean_chain_id, artifact_store=store)
        if not chain_context["artifacts"]:
            return render_output(
                chain_id=clean_chain_id,
                error=chain_context["error"],
            )

        summary = SynthesisService(ModelRouter(artifact_store=store), store).synthesize(clean_chain_id)
        latest = chain_context["latest"] or {}
        event_result = EventStore().write_event(
            EventRecord(
                event_type="research_synthesis",
                source="research_ui",
                retrieved_at=_utc_now_iso(),
                event_timestamp=_utc_now_iso(),
                source_ref=clean_chain_id,
                symbol=str(latest.get("ticker") or ""),
                headline=f"Research synthesis {clean_chain_id[:8]}",
                detail=summary[:240],
                provenance_descriptor={
                    "chain_id": clean_chain_id,
                    "artifact_count": chain_context["artifact_count"],
                    "latest_artifact_id": latest.get("artifact_id") or "",
                },
                payload={
                    "chain_id": clean_chain_id,
                    "artifact_count": chain_context["artifact_count"],
                    "latest_artifact_id": latest.get("artifact_id") or "",
                    "latest_artifact_type": latest.get("artifact_type") or "",
                    "ticker": latest.get("ticker") or "",
                    "summary": summary,
                },
            )
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Synthesis failed: {exc}",
        )

    return render_output(
        chain_id=clean_chain_id,
        synthesis={
            "event_id": str(event_result.get("id") or ""),
            "chain_id": clean_chain_id,
            "ticker": str(latest.get("ticker") or ""),
            "artifact_count": chain_context["artifact_count"],
            "latest_artifact_id": str(latest.get("artifact_id") or ""),
            "latest_artifact_type": str(latest.get("artifact_label") or latest.get("artifact_type") or ""),
            "summary": summary,
        },
    )


@router.post("/api/actions/research/post-mortem", response_class=HTMLResponse)
def research_post_mortem_action(
    request: Request,
    chain_id: str = Form(default=""),
    hypothesis_id: str = Form(default=""),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_hypothesis_id = hypothesis_id.strip() if isinstance(hypothesis_id, str) else ""
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    store = ArtifactStore()

    if not clean_hypothesis_id:
        if not clean_chain_id:
            return render_output(
                error="Provide chain_id or hypothesis_id to generate a post-mortem.",
            )
        hypothesis = _find_chain_artifact(
            clean_chain_id,
            ArtifactType.HYPOTHESIS_CARD,
            artifact_store=store,
        )
        if hypothesis is None:
            return render_output(
                chain_id=clean_chain_id,
                error=f"No hypothesis artifact found for chain {clean_chain_id[:8]}.",
            )
        clean_hypothesis_id = str(hypothesis.artifact_id or "")

    try:
        artifact = PostMortemService(ModelRouter(artifact_store=store), store).generate_post_mortem(
            clean_hypothesis_id
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Post-mortem generation failed: {exc}",
        )

    from app.api.server import _serialize_research_artifact
    return render_output(
        chain_id=str(artifact.chain_id or clean_chain_id),
        post_mortem=_serialize_research_artifact(artifact),
    )


@router.post("/api/actions/research/pilot-approve", response_class=HTMLResponse)
def research_pilot_approve_action(
    request: Request,
    chain_id: str = Form(default=""),
    actor: str = Form(default="operator"),
    notes: str = Form(default="Pilot approved by operator."),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else "operator"
    clean_notes = notes.strip() if isinstance(notes, str) else "Pilot approved by operator."
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    if not clean_chain_id:
        return render_output(
            error="Research chain_id is required for pilot approval.",
        )

    try:
        artifact = PilotSignoffService(
            artifact_store=ArtifactStore(),
            pipeline_state_updater=lambda cid, stage, outcome, detail: _update_research_pipeline_state(
                cid,
                stage,
                outcome=outcome,
                operator_ack=True,
                operator_notes=detail,
            ),
        ).approve_pilot(
            chain_id=clean_chain_id,
            actor=clean_actor or "operator",
            notes=clean_notes,
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Pilot approval failed: {exc}",
        )

    from app.api.server import _serialize_research_artifact
    _invalidate_research_cached_values()
    return render_output(
        chain_id=clean_chain_id,
        pilot_decision=_serialize_research_artifact(artifact),
    )


@router.post("/api/actions/research/pilot-reject", response_class=HTMLResponse)
def research_pilot_reject_action(
    request: Request,
    chain_id: str = Form(default=""),
    actor: str = Form(default="operator"),
    notes: str = Form(default="Pilot rejected by operator."),
    queue_lane: str = Form(default="all"),
    active_view: str = Form(default="all"),
):
    clean_chain_id = chain_id.strip() if isinstance(chain_id, str) else ""
    clean_actor = actor.strip() if isinstance(actor, str) else "operator"
    clean_notes = notes.strip() if isinstance(notes, str) else "Pilot rejected by operator."
    clean_queue_lane = _normalize_research_queue_lane(queue_lane)
    clean_active_view = _normalize_research_active_view(active_view)

    def render_output(**kwargs):
        return _render_research_operator_output(
            request,
            queue_lane=clean_queue_lane,
            active_view=clean_active_view,
            **kwargs,
        )

    if not clean_chain_id:
        return render_output(
            error="Research chain_id is required for pilot rejection.",
        )

    try:
        artifact = PilotSignoffService(
            artifact_store=ArtifactStore(),
            pipeline_state_updater=lambda cid, stage, outcome, detail: _update_research_pipeline_state(
                cid,
                stage,
                outcome=outcome,
                operator_ack=True,
                operator_notes=detail,
            ),
        ).reject_pilot(
            chain_id=clean_chain_id,
            actor=clean_actor or "operator",
            notes=clean_notes,
        )
    except Exception as exc:
        return render_output(
            chain_id=clean_chain_id,
            error=f"Pilot rejection failed: {exc}",
        )

    from app.api.server import _serialize_research_artifact
    _invalidate_research_cached_values()
    return render_output(
        chain_id=clean_chain_id,
        pilot_decision=_serialize_research_artifact(artifact),
    )


@router.post("/api/actions/run-daily-dag", response_class=HTMLResponse)
def run_daily_dag_action():
    job_id = str(uuid.uuid4())
    create_job(job_id=job_id, job_type="daily_dag", status="running", detail="Full pipeline DAG")

    def _run_dag_job(jid: str):
        try:
            result = control.trigger_daily_dag()
            update_job(jid, status="completed", result=json.dumps(result))
        except Exception as exc:
            update_job(jid, status="failed", error=str(exc))

    thread = threading.Thread(target=_run_dag_job, args=(job_id,), daemon=True)
    thread.start()
    return action_message(f"Daily DAG started (job {job_id[:8]})", ok=True)


@router.post("/api/actions/discover-options", response_class=HTMLResponse)
def discover_options_action(
    mode: str = Form(default="search"),
    include_details: str = Form(default="on"),
    strikes: str = Form(default=""),
):
    job_id = str(uuid.uuid4())
    details = str(include_details).lower() in {"on", "true", "1", "yes"}
    detail = f"mode={mode} details={details} strikes={strikes or '-'}"
    create_job(job_id=job_id, job_type="discover_options", status="queued", detail=detail)
    thread = threading.Thread(
        target=_run_discovery_job,
        args=(job_id, mode.strip().lower(), details, strikes.strip()),
        daemon=True,
    )
    thread.start()
    return action_message(f"Queued options discovery job {job_id[:8]}.", ok=True)


@router.post("/api/actions/calibrate-options", response_class=HTMLResponse)
def calibrate_options_action(
    index_filter: str = Form(default=""),
    verbose: str = Form(default=""),
):
    job_id = str(uuid.uuid4())
    verbose_flag = str(verbose).lower() in {"on", "true", "1", "yes"}
    detail = f"index={index_filter or 'all'} verbose={verbose_flag}"
    create_job(job_id=job_id, job_type="calibrate_options", status="queued", detail=detail)
    thread = threading.Thread(
        target=_run_calibration_job,
        args=(job_id, index_filter.strip(), verbose_flag),
        daemon=True,
    )
    thread.start()
    return action_message(f"Queued calibration job {job_id[:8]}.", ok=True)


@router.post("/api/actions/strategy-params/create", response_class=HTMLResponse)
def strategy_params_create_action(
    strategy_key: str = Form(default=config.DEFAULT_STRATEGY_KEY),
    name: str = Form(default=""),
    status: str = Form(default="shadow"),
    source_run_id: str = Form(default=""),
    overrides_json: str = Form(default=""),
    notes: str = Form(default=""),
    actor: str = Form(default="operator"),
):
    job_id = str(uuid.uuid4())
    create_job(job_id=job_id, job_type="strategy_params_create", status="running")
    clean_strategy = strategy_key.strip().lower()
    clean_name = name.strip()
    clean_status = status.strip().lower() or "shadow"

    if clean_strategy != config.DEFAULT_STRATEGY_KEY:
        msg = f"Unsupported strategy '{strategy_key}'."
        update_job(job_id, status="failed", error=msg)
        return action_message(msg, ok=False)

    params = dict(config.IBS_CREDIT_SPREAD_PARAMS)
    clean_source = source_run_id.strip()
    if clean_source:
        run = get_calibration_run(clean_source)
        if run:
            params["calibration_run_id"] = clean_source
            if run.get("overall_ratio") is not None:
                params["ig_pricing_ratio"] = run.get("overall_ratio")

    clean_overrides = overrides_json.strip()
    if clean_overrides:
        try:
            parsed = json.loads(clean_overrides)
        except json.JSONDecodeError as exc:
            msg = f"Invalid overrides JSON: {exc}"
            update_job(job_id, status="failed", error=msg)
            return action_message(msg, ok=False)
        if not isinstance(parsed, dict):
            msg = "Overrides JSON must be an object."
            update_job(job_id, status="failed", error=msg)
            return action_message(msg, ok=False)
        params.update(parsed)

    if not clean_name:
        clean_name = f"{clean_strategy}-set-{job_id[:8]}"

    try:
        created = create_strategy_parameter_set(
            strategy_key=clean_strategy,
            name=clean_name,
            parameters_payload=json.dumps(params, default=str, sort_keys=True),
            status=clean_status,
            source_run_id=clean_source or None,
            notes=notes.strip() or None,
            created_by=actor.strip() or "operator",
        )
    except Exception as exc:
        msg = str(exc)
        update_job(job_id, status="failed", error=msg)
        return action_message(msg, ok=False)

    detail = f"set={created['id'][:8]} v{created['version']} status={created['status']}"
    update_job(job_id, status="completed", detail=detail)
    return action_message(f"Saved parameter set {created['id'][:8]} (v{created['version']}).", ok=True)


@router.post("/api/actions/strategy-params/promote", response_class=HTMLResponse)
def strategy_params_promote_action(
    set_id: str = Form(default=""),
    target_status: str = Form(default="staged_live"),
    actor: str = Form(default="operator"),
    acknowledgement: str = Form(default=""),
    note: str = Form(default=""),
):
    clean_set_id = set_id.strip()
    clean_ack = acknowledgement.strip()
    clean_target = target_status.strip().lower() or "staged_live"
    if not clean_set_id:
        return action_message("set_id is required.", ok=False)
    if not clean_ack:
        return action_message("acknowledgement is required.", ok=False)

    set_item = get_strategy_parameter_set(clean_set_id)
    if not set_item:
        return action_message(f"Parameter set '{clean_set_id}' not found.", ok=False)

    allowed, reason_codes = validate_lane_transition(
        from_status=str(set_item.get("status") or ""),
        to_status=clean_target,
    )
    if not allowed:
        reasons = ", ".join(reason_codes) or "INVALID_LANE_TRANSITION"
        return action_message(
            f"Promotion blocked by 3-lane policy ({reasons}).",
            ok=False,
        )

    if clean_target in {"staged_live", "live"}:
        gate = build_promotion_gate_report(
            strategy_key=str(set_item.get("strategy_key") or config.DEFAULT_STRATEGY_KEY),
        )
        expected_action = (
            "PROMOTE_SHADOW_TO_STAGED"
            if clean_target == "staged_live"
            else "PROMOTE_STAGED_TO_LIVE"
        )
        recommendation = gate.get("recommendation", {})
        rec_action = str(recommendation.get("action") or "HOLD")
        rec_target = recommendation.get("target_set_id")
        rec_reasons = recommendation.get("reason_codes") or []
        if rec_action != expected_action:
            return action_message(
                f"Promotion blocked by gate ({rec_action}): {', '.join(rec_reasons) or 'NO_REASON'}",
                ok=False,
            )
        if rec_target and rec_target != clean_set_id:
            return action_message(
                f"Promotion blocked by gate target mismatch (expected {str(rec_target)[:8]}).",
                ok=False,
            )

    job_id = str(uuid.uuid4())
    create_job(
        job_id=job_id,
        job_type="strategy_params_promote",
        status="running",
        detail=f"set={clean_set_id[:8]} -> {clean_target}",
    )
    result = promote_strategy_parameter_set(
        set_id=clean_set_id,
        to_status=clean_target,
        actor=actor,
        acknowledgement=clean_ack,
        note=note.strip() or None,
    )
    if result.get("ok"):
        update_job(job_id, status="completed", detail=result.get("message"))
        return action_message(result.get("message", "Promotion complete."), ok=True)
    update_job(job_id, status="failed", error=result.get("message"))
    return action_message(result.get("message", "Promotion failed."), ok=False)


# ===========================================================================
# Research API routes (GET /api/...)
# ===========================================================================

@router.get("/api/pipeline-status")
def pipeline_status_api():
    return control.pipeline_status()


@router.get("/api/research/artifact-chain/{chain_id}")
def research_artifact_chain_api(chain_id: str):
    from app.api.server import _build_research_artifact_chain_context
    context = _build_research_artifact_chain_context(chain_id)
    if not context["artifacts"]:
        raise HTTPException(status_code=404, detail=context["error"])
    return context


@router.get("/api/research/artifact/{artifact_id}")
def research_artifact_detail_api(artifact_id: str):
    from app.api.server import _build_research_artifact_detail
    artifact = _build_research_artifact_detail(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Research artifact not found: {artifact_id}")
    return artifact


@router.get("/api/calibration/runs")
def api_calibration_runs(limit: int = 20):
    return {"items": get_calibration_runs(limit=limit)}


@router.get("/api/calibration/points")
def api_calibration_points(
    run_id: str,
    limit: int = 200,
    index_name: str = "",
    ticker: str = "",
    expiry_type: str = "",
    strike_min: Optional[float] = None,
    strike_max: Optional[float] = None,
):
    return {
        "items": get_calibration_points(
            run_id=run_id,
            limit=limit,
            index_name=index_name or None,
            ticker=ticker or None,
            expiry_type=expiry_type or None,
            strike_min=strike_min,
            strike_max=strike_max,
        )
    }


@router.get("/api/strategy/parameter-sets")
def api_strategy_parameter_sets(limit: int = 50, strategy_key: str = "", status: str = ""):
    return {
        "items": get_strategy_parameter_sets(
            limit=limit,
            strategy_key=strategy_key or None,
            status=status or None,
        )
    }


@router.get("/api/strategy/promotions")
def api_strategy_promotions(limit: int = 50, strategy_key: str = ""):
    return {
        "items": get_strategy_promotions(
            limit=limit,
            strategy_key=strategy_key or None,
        )
    }


@router.get("/api/strategy/active")
def api_strategy_active(strategy_key: str = config.DEFAULT_STRATEGY_KEY):
    return {
        "shadow": get_active_strategy_parameter_set(strategy_key, status="shadow"),
        "staged_live": get_active_strategy_parameter_set(strategy_key, status="staged_live"),
        "live": get_active_strategy_parameter_set(strategy_key, status="live"),
    }


@router.get("/api/strategy/promotion-gate")
def api_strategy_promotion_gate(
    strategy_key: str = config.DEFAULT_STRATEGY_KEY,
    cooldown_hours: int = 24,
):
    return build_promotion_gate_report(
        strategy_key=strategy_key,
        cooldown_hours=cooldown_hours,
    )


@router.get("/api/risk/briefing")
def api_risk_briefing():
    return build_risk_briefing_payload()


@router.get("/api/signal-shadow")
def api_signal_shadow():
    return enrich_signal_shadow_payload(get_signal_shadow_report())


@router.get("/api/execution-quality")
def api_execution_quality(days: int = 30):
    return get_execution_quality_payload(days=days)


@router.get("/api/analytics/portfolio")
def api_portfolio_analytics(days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS):
    return build_portfolio_analytics_payload(days=days)


@router.get("/api/charts/equity-curve")
def api_equity_curve(days: int = 90):
    rows = get_fund_daily_reports(days=days)
    result = []
    for r in rows:
        if r.get("report_date") and r.get("total_nav") is not None:
            result.append({
                "time": r["report_date"],
                "value": round(float(r["total_nav"]), 2),
            })
    result.sort(key=lambda x: x["time"])
    return result


@router.post("/api/backtest")
def submit_backtest(request_body: dict[str, Any] | None = None):
    """Submit a backtest job for a strategy + date range."""
    if request_body is None:
        request_body = {}

    strategy = str(request_body.get("strategy") or "").strip()
    start_date = str(request_body.get("start_date") or "").strip()
    end_date = str(request_body.get("end_date") or "").strip()
    tickers = request_body.get("tickers") or []

    if not strategy:
        return JSONResponse(
            {"ok": False, "error": "MISSING_STRATEGY", "detail": "strategy is required"},
            status_code=422,
        )

    job_id = str(uuid.uuid4())
    create_job(
        job_id=job_id,
        job_type="backtest",
        status="running",
        mode="backtest",
        detail=json.dumps({
            "strategy": strategy,
            "start_date": start_date or None,
            "end_date": end_date or None,
            "tickers": tickers,
        }),
    )

    # Launch backtest in background thread
    def _run_backtest():
        try:
            from analytics.backtester import Backtester
            bt = Backtester(lookback_days=750)
            result = bt.run(
                strategy_name=strategy,
                tickers=tickers or None,
                start_date=start_date or None,
                end_date=end_date or None,
            )
            summary = {
                "total_return_pct": round(result.total_return_pct, 2),
                "sharpe_ratio": round(result.sharpe, 4) if result.sharpe else None,
                "max_drawdown_pct": round(result.max_drawdown_pct, 2),
                "total_trades": result.total_trades,
                "win_rate": round(result.win_rate * 100, 1) if result.win_rate else None,
                "profit_factor": round(result.profit_factor, 2) if result.profit_factor else None,
            }
            update_job(job_id, status="completed", result=json.dumps(summary))
        except Exception as e:
            update_job(job_id, status="failed", error=str(e))

    thread = threading.Thread(target=_run_backtest, daemon=True)
    thread.start()

    return {"ok": True, "job_id": job_id, "message": f"Backtest '{strategy}' submitted."}


@router.get("/api/backtest/{job_id}")
def get_backtest_result(job_id: str):
    """Get status/results of a backtest job."""
    job = get_job(job_id.strip())
    if not job:
        return JSONResponse(
            {"ok": False, "error": "NOT_FOUND", "detail": f"Job {job_id} not found"},
            status_code=404,
        )
    result = _parse_job_result(job.get("result") or "")
    return {
        "ok": True,
        "job_id": job_id,
        "status": job.get("status"),
        "result": result,
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


# ===========================================================================
# Ideas API routes
# ===========================================================================

@router.get("/api/ideas")
def list_ideas(stage: str = None, ticker: str = None, limit: int = 50):
    """List trade ideas with optional filters."""
    ideas = get_trade_ideas(stage=stage, ticker=ticker, limit=limit)
    return {"ideas": ideas, "count": len(ideas)}


@router.get("/api/ideas/{idea_id}")
def get_idea_detail(idea_id: str):
    """Get a single idea with its transition history."""
    idea = get_trade_idea(idea_id)
    if not idea:
        return JSONResponse({"error": "Idea not found"}, status_code=404)
    transitions = get_idea_transitions(idea_id)
    return {"idea": idea, "transitions": transitions}


@router.post("/api/ideas/{idea_id}/promote")
async def promote_idea_endpoint(idea_id: str, request: Request):
    """Promote an idea to the next pipeline stage.

    DB writes run in threadpool to avoid blocking the async event loop.
    """
    from intelligence.idea_pipeline import IdeaPipelineManager
    ct = request.headers.get("content-type", "")
    if "json" in ct:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
    target = body.get("target_stage", "")
    reason = body.get("reason", "")

    def _do():
        mgr = IdeaPipelineManager()
        return mgr.promote_idea(idea_id, target, actor="operator", reason=reason)

    result = await asyncio.to_thread(_do)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/ideas/{idea_id}/reject")
async def reject_idea_endpoint(idea_id: str, request: Request):
    """Reject an idea.

    DB writes run in threadpool to avoid blocking the async event loop.
    """
    ct = request.headers.get("content-type", "")
    if "json" in ct:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
    reason = body.get("reason", "")

    def _do():
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        return mgr.reject_idea(idea_id, reason=reason, actor="operator")

    result = await asyncio.to_thread(_do)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/ideas/{idea_id}/backtest")
def trigger_idea_backtest(idea_id: str):
    """Promote to backtest stage (if needed) and trigger backtest."""
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager()
    idea = get_trade_idea(idea_id)
    if not idea:
        return JSONResponse({"success": False, "reasons": ["IDEA_NOT_FOUND"]}, status_code=404)
    # Auto-promote to backtest if still in review
    if idea["pipeline_stage"] == "review":
        promo = mgr.promote_idea(idea_id, "backtest", actor="operator", reason="Backtest requested")
        if not promo.get("success"):
            return JSONResponse(promo, status_code=400)
    result = mgr.trigger_backtest(idea_id)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/ideas/{idea_id}/paper")
def start_idea_paper(idea_id: str):
    """Start a paper trade for an idea."""
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager()
    result = mgr.start_paper_trade(idea_id)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/ideas/{idea_id}/paper/close")
async def close_idea_paper(idea_id: str, request: Request):
    """Close a paper trade.

    DB writes run in threadpool to avoid blocking the async event loop.
    """
    ct = request.headers.get("content-type", "")
    if "json" in ct:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
    reason = body.get("reason", "")

    def _do():
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        return mgr.close_paper_trade(idea_id, reason=reason)

    result = await asyncio.to_thread(_do)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@router.get("/api/ideas/{idea_id}/paper/status")
def idea_paper_status(idea_id: str):
    """Get paper trade P&L status."""
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager()
    return mgr.get_paper_trade_status(idea_id)


@router.post("/api/ideas/{idea_id}/notes")
async def update_idea_notes(idea_id: str, request: Request):
    """Add user notes to an idea.

    DB writes run in threadpool to avoid blocking the async event loop.
    """
    from data.trade_db import update_trade_idea
    ct = request.headers.get("content-type", "")
    if "json" in ct:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
    notes = body.get("notes", "")

    def _do():
        idea = get_trade_idea(idea_id)
        if not idea:
            return None
        existing = idea.get("user_notes") or ""
        new_notes = f"{existing}\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {notes}".strip()
        update_trade_idea(idea_id, user_notes=new_notes)
        return True

    result = await asyncio.to_thread(_do)
    if result is None:
        return JSONResponse({"error": "Idea not found"}, status_code=404)
    return {"success": True}


@router.post("/api/ideas/backfill")
def backfill_ideas():
    """Backfill trade ideas from existing council analyses."""
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager()
    count = mgr.backfill_ideas_from_events()
    return {"success": True, "created": count}


@router.get("/api/ideas/{idea_id}/research")
def get_idea_research(idea_id: str):
    """Get research steps with outputs for an idea."""
    from data.trade_db import get_research_steps
    idea = get_trade_idea(idea_id)
    if not idea:
        return JSONResponse({"error": "Idea not found"}, status_code=404)
    steps = get_research_steps(idea_id)
    # Parse output_json for each step
    for step in steps:
        if step.get("output_json"):
            try:
                step["output"] = json.loads(step["output_json"])
            except (json.JSONDecodeError, TypeError):
                step["output"] = None
    return {
        "idea_id": idea_id,
        "review_score": idea.get("review_score"),
        "review_verdict": idea.get("review_verdict"),
        "strategy_spec": json.loads(idea["strategy_spec_json"]) if idea.get("strategy_spec_json") else None,
        "steps": steps,
    }


@router.post("/api/ideas/{idea_id}/research/start")
def start_idea_research(idea_id: str):
    """Manually trigger research for an idea."""
    from intelligence.idea_research import IdeaResearcher
    idea = get_trade_idea(idea_id)
    if not idea:
        return JSONResponse({"error": "Idea not found"}, status_code=404)
    researcher = IdeaResearcher()
    job_id = researcher.run_async(idea_id)
    return {"success": True, "job_id": job_id, "idea_id": idea_id}


# ===========================================================================
# Brief routes
# ===========================================================================

@router.post("/api/actions/generate-brief", response_class=HTMLResponse)
def generate_brief_action(type: str = "morning"):
    """Generate a market brief on demand."""
    brief_type = type if type in {"morning", "evening"} else "morning"
    try:
        brief = generate_brief(brief_type=brief_type)
        with _BRIEF_LOCK:
            _LATEST_BRIEFS[brief_type] = brief
        return TEMPLATES.TemplateResponse(
            Request(scope={"type": "http", "method": "GET", "path": "/"}),
            "_market_brief.html",
            {"request": Request(scope={"type": "http", "method": "GET", "path": "/"}), "brief": brief.to_dict()},
        )
    except Exception as exc:
        return action_message(f"Brief generation failed: {exc}", ok=False)


@router.get("/api/briefs/latest")
def api_latest_briefs():
    """Return latest morning and evening briefs as JSON."""
    with _BRIEF_LOCK:
        return {
            k: v.to_dict() for k, v in _LATEST_BRIEFS.items()
        }


@router.get("/api/briefs/{brief_type}")
def api_brief(brief_type: str):
    """Return a specific brief type."""
    with _BRIEF_LOCK:
        brief = _LATEST_BRIEFS.get(brief_type)
    if not brief:
        return {"error": f"No {brief_type} brief available"}
    return brief.to_dict()
