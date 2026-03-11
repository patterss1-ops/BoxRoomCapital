"""FastAPI app entry point for the control plane."""
from __future__ import annotations

import html
import ipaddress
import logging
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import re as _re


from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

import config
from app.api.ledger import router as ledger_router
from app.engine.control import BotControlService
from app.engine.signal_shadow import get_signal_shadow_report, run_signal_shadow_cycle
from app.research.service import ResearchService
from analytics.portfolio_analytics import (
    compute_drawdowns,
    compute_metrics,
    compute_rolling_stats,
)
from fund.promotion_gate import build_promotion_gate_report, validate_lane_transition
from fund.nav import calculate_fund_nav
from intelligence.jobs.signal_layer_jobs import (
    build_ranked_candidates,
    enrich_signal_shadow_payload,
    run_tier1_shadow_jobs,
    summarize_research_overlay,
)
from data.trade_db import (
    DB_PATH,
    complete_calibration_run,
    create_job,
    create_calibration_run,
    create_strategy_parameter_set,
    get_bot_events,
    get_calibration_run,
    get_calibration_points,
    get_calibration_runs,
    get_control_actions,
    get_fund_daily_reports,
    get_job,
    get_active_strategy_parameter_set,
    get_incidents,
    get_ledger_reconcile_report,
    get_jobs,
    get_open_option_positions,
    get_unified_ledger_snapshot,
    get_option_contract_summary,
    get_option_contracts,
    get_order_actions,
    get_strategy_parameter_sets,
    get_strategy_parameter_set,
    get_strategy_promotions,
    get_summary,
    get_conn,
    init_db,
    insert_calibration_points,
    log_event,
    promote_strategy_parameter_set,
    update_job,
    get_trade_idea,
    get_trade_ideas,
    get_trade_ideas_by_analysis,
    get_idea_transitions,
)
from intelligence.webhook_server import (
    NormalizedTradingViewAlert,
    TradingViewStrategySpec,
    WebhookValidationError,
    build_audit_detail,
    extract_auth_token,
    get_tradingview_strategy_registry,
    normalize_tradingview_alert,
    parse_json_payload,
    summarize_payload,
    validate_expected_token,
)
from execution.order_intent import OrderIntent, OrderSide
from execution.dispatcher import default_broker_resolver
from execution.policy.capability_policy import RouteAccountType, StrategyRequirements
from execution.policy.route_policy import RoutePolicyState
from execution.router import AccountRouter, RouteConfigEntry, RouteIntent
from data.order_intent_store import create_order_intent_envelope
from fund.execution_quality import get_execution_quality_payload
from fund.promotion_gate import PromotionGateConfig, evaluate_promotion_gate
from app.metrics import build_api_health_payload, build_prometheus_metrics_payload
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    ExecutionReport,
    PromotionOutcome,
    RebalanceSheet,
    ReviewTrigger,
    RiskLimits as ResearchRiskLimits,
    SizingSpec,
    TradeSheet,
)
from research.dashboard import ResearchDashboardService
from research.engine_b.source_scoring import SourceScoringService
from research.model_router import ModelRouter
from research.readiness import build_research_readiness_report
from research.shared.decay_review import DecayReviewService
from research.shared.kill_monitor import KillMonitor
from research.shared.pilot_signoff import PilotSignoffService
from research.shared.post_mortem import PostMortemService
from research.shared.synthesis import SynthesisService
from research.runtime import build_engine_a_pipeline, build_engine_b_pipeline
from intelligence.event_store import EventRecord, EventStore, compute_event_id
from intelligence.feature_store import FeatureStore
from risk.pre_trade_gate import RiskContext, RiskLimits as PreTradeRiskLimits, RiskOrderRequest, evaluate_pre_trade_risk
from risk.portfolio_risk import get_risk_briefing
from intelligence.intel_pipeline import (
    IntelSubmission,
    analyze_intel_async,
)
from intelligence.market_brief import MarketBrief, generate_brief, get_email_draft_content
from intelligence.sa_factor_grades import store_factor_grades
from app.api import research_artifact_views as _research_artifact_views
from app.api import app_factory as _app_factory
from app.api import broker_helpers as _broker_helpers
from app.api import fragment_context_helpers as _fragment_context_helpers
from app.api import job_helpers as _job_helpers
from app.api import route_compat as _route_compat
from app.api import research_operator_helpers as _research_operator_helpers
from app.api import research_dashboard_contexts as _research_dashboard_contexts
from app.api import research_engine_a_views as _research_engine_a_views
from app.api import intel_intake_helpers as _intel_intake_helpers
from app.api import operator_surface_helpers as _operator_surface_helpers
from app.api import research_workbench_views as _research_workbench_views
from app.api import settings_helpers as _settings_helpers
from app.api import social_bookmarklet_helpers as _social_bookmarklet_helpers
from app.api import tradingview_helpers as _tradingview_helpers
from app.api.shared import (
    PROJECT_ROOT,
    TEMPLATES,
    control,
    research,
    research_dashboard,
    _broker,
    _broker_lock,
    _get_or_create_broker,
    _get_cached_value,
    _invalidate_cached_values,
    _invalidate_research_cached_values,
    _parse_iso_datetime,
    _relative_time_label,
    _run_preflight_checks,
    action_message,
    _telegram_reply,
    _telegram_reply_long,
    _FRAGMENT_CACHE,
    _FRAGMENT_CACHE_LOCK,
    _FRAGMENT_CACHE_REFRESH_LOCKS,
    _RESEARCH_FRAGMENT_CACHE_KEYS,
    _STATUS_CACHE_TTL_SECONDS,
    _BROKER_SNAPSHOT_CACHE_TTL_SECONDS,
    _BROKER_HEALTH_CACHE_TTL_SECONDS,
    _MARKET_BROWSER_CACHE_TTL_SECONDS,
    _RISK_BRIEFING_CACHE_TTL_SECONDS,
    _INTELLIGENCE_FEED_CACHE_TTL_SECONDS,
    _PORTFOLIO_ANALYTICS_CACHE_TTL_SECONDS,
    _RESEARCH_CACHE_TTL_SECONDS,
    _LEDGER_CACHE_TTL_SECONDS,
    _ACTIVE_INCIDENT_EVENT_LOOKBACK,
    _EVENT_STREAM_HEARTBEAT_SECONDS,
    _ENGINE_B_SOURCE_SCORING,
    _INTEL_ANALYSIS_STALE_SECONDS,
    _RESEARCH_SYNTHESIS_EVENT_TYPE,
    _RESEARCH_OPERATOR_SOURCE,
    _RESEARCH_ARCHIVE_VIEWS,
    _LATEST_BRIEFS,
    _BRIEF_LOCK,
    _TRADINGVIEW_RISK_LIMITS,
    _UI_BROKER_TIMEOUT_SECONDS,
    _UI_BROKER_MARKET_TIMEOUT_SECONDS,
)
logger = logging.getLogger(__name__)

_RESEARCH_CHAIN_LIFECYCLE_STAGES = _research_artifact_views._RESEARCH_CHAIN_LIFECYCLE_STAGES
_serialize_research_artifact = _research_artifact_views._serialize_research_artifact
_serialize_research_synthesis_event = _research_artifact_views._serialize_research_synthesis_event
_extract_research_artifact_note = _research_artifact_views._extract_research_artifact_note
_build_research_chain_lifecycle = _research_artifact_views._build_research_chain_lifecycle
_build_research_lane_focus = _research_workbench_views._build_research_lane_focus
_normalize_research_queue_lane = _research_workbench_views._normalize_research_queue_lane
_normalize_research_active_view = _research_workbench_views._normalize_research_active_view
_research_queue_lane_label = _research_workbench_views._research_queue_lane_label
_research_active_view_for_lane = _research_workbench_views._research_active_view_for_lane
_research_active_view_label = _research_workbench_views._research_active_view_label


def _build_research_artifact_chain_context(
    chain_id: str,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    store = artifact_store if artifact_store is not None else ArtifactStore()
    return _research_artifact_views._build_research_artifact_chain_context(
        chain_id,
        artifact_store=store,
    )


def _build_research_artifact_detail(
    artifact_id: str,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any] | None:
    store = artifact_store if artifact_store is not None else ArtifactStore()
    return _research_artifact_views._build_research_artifact_detail(
        artifact_id,
        artifact_store=store,
    )


from utils.datetime_utils import utc_now_iso as _utc_now_iso


def _expire_stale_intel_analysis_jobs(now: datetime | None = None) -> int:
    return _intel_intake_helpers._expire_stale_intel_analysis_jobs(
        get_conn=get_conn,
        db_path=DB_PATH,
        parse_iso_datetime=_parse_iso_datetime,
        update_job=update_job,
        stale_seconds=_INTEL_ANALYSIS_STALE_SECONDS,
        now=now,
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
    return _intel_intake_helpers._queue_engine_b_intake(
        raw_content=raw_content,
        source_class=source_class,
        source_ids=source_ids,
        detail=detail,
        score_source=_ENGINE_B_SOURCE_SCORING.score_source,
        create_job=create_job,
        update_job=update_job,
        submit_engine_b_event=control.submit_engine_b_event,
        invalidate_research_cached_values=_invalidate_research_cached_values,
        job_type=job_type,
        source_credibility=source_credibility,
        allow_ad_hoc=allow_ad_hoc,
    )


def _build_research_operator_output_context(
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
) -> dict[str, Any]:
    return _research_workbench_views._build_research_operator_output_context(
        chain_id=chain_id,
        queue_lane=queue_lane,
        active_view=active_view,
        synthesis=synthesis,
        operator_action=operator_action,
        pilot_decision=pilot_decision,
        post_mortem=post_mortem,
        queued_intake=queued_intake,
        error=error,
        build_research_artifact_chain_context=_build_research_artifact_chain_context,
        build_research_workbench_queue_alignment=lambda chain_context, selected_queue_lane: (
            _research_workbench_views._build_research_workbench_queue_alignment(
                chain_context,
                selected_queue_lane,
                research_queue_lane_label=_research_queue_lane_label,
                research_active_view_label=_research_active_view_label,
            )
        ),
        build_research_queue_follow_up_context=lambda selected_queue_lane, exclude_chain_id="": (
            _research_workbench_views._build_research_queue_follow_up_context(
                selected_queue_lane,
                exclude_chain_id=exclude_chain_id,
                get_research_alerts_context=_get_research_alerts_context,
            )
        ),
        build_research_operating_summary_context=lambda: _research_dashboard_contexts._get_research_operating_summary_context(
            get_cached_value=_get_cached_value,
            operating_summary=research_dashboard.operating_summary,
            build_engine_a_rebalance_panel_context=_build_engine_a_rebalance_panel_context,
            build_research_queue_follow_up_context=lambda selected_queue_lane, exclude_chain_id="": (
                _research_workbench_views._build_research_queue_follow_up_context(
                    selected_queue_lane,
                    exclude_chain_id=exclude_chain_id,
                    get_research_alerts_context=_get_research_alerts_context,
                )
            ),
            normalize_research_queue_lane=_normalize_research_queue_lane,
            utc_now_iso=_utc_now_iso,
            logger=logger,
        ),
        utc_now_iso=_utc_now_iso,
    )

def _build_research_archive_context(
    *,
    limit: int = 6,
    ticker: str = "",
    search_text: str = "",
    view: str = "all",
    artifact_store: ArtifactStore | None = None,
    event_store: EventStore | None = None,
) -> dict[str, Any]:
    store = artifact_store if artifact_store is not None else ArtifactStore()
    events = event_store if event_store is not None else EventStore()
    return _research_workbench_views._build_research_archive_context(
        limit=limit,
        ticker=ticker,
        search_text=search_text,
        view=view,
        artifact_store=store,
        event_store=events,
        serialize_research_synthesis_event=_serialize_research_synthesis_event,
        serialize_research_artifact=_serialize_research_artifact,
        build_research_chain_lifecycle=_build_research_chain_lifecycle,
        extract_research_artifact_note=_extract_research_artifact_note,
        research_archive_views=_RESEARCH_ARCHIVE_VIEWS,
        research_synthesis_event_type=_RESEARCH_SYNTHESIS_EVENT_TYPE,
        utc_now_iso=_utc_now_iso,
    )

_update_research_pipeline_state = _research_operator_helpers._update_research_pipeline_state
_operator_created_by = _research_operator_helpers._operator_created_by


_supersede_rebalance_sheet = _research_operator_helpers._supersede_rebalance_sheet
_manual_engine_a_broker_target = _research_operator_helpers._manual_engine_a_broker_target
_parse_contract_details = _research_operator_helpers._parse_contract_details
_build_manual_engine_a_trade_instruments = _research_operator_helpers._build_manual_engine_a_trade_instruments


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


_latest_artifact_by_type = _research_operator_helpers._latest_artifact_by_type


def _build_engine_a_regime_panel_context(
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    return _research_engine_a_views._build_engine_a_regime_panel_context(
        artifact_store=artifact_store,
        latest_artifact_by_type=_latest_artifact_by_type,
        utc_now_iso=_utc_now_iso,
    )


def _build_engine_a_signal_heatmap_context(
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    return _research_engine_a_views._build_engine_a_signal_heatmap_context(
        artifact_store=artifact_store,
        latest_artifact_by_type=_latest_artifact_by_type,
        utc_now_iso=_utc_now_iso,
    )


def _build_engine_a_portfolio_targets_context(
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    return _research_engine_a_views._build_engine_a_portfolio_targets_context(
        artifact_store=artifact_store,
        latest_artifact_by_type=_latest_artifact_by_type,
        utc_now_iso=_utc_now_iso,
    )


def _build_engine_a_rebalance_panel_context(
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    return _research_engine_a_views._build_engine_a_rebalance_panel_context(
        artifact_store=artifact_store,
        artifact_store_factory=ArtifactStore,
        latest_artifact_by_type=_latest_artifact_by_type,
        utc_now_iso=_utc_now_iso,
    )


def _build_engine_a_regime_journal_context(
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    return _research_engine_a_views._build_engine_a_regime_journal_context(
        artifact_store=artifact_store,
        artifact_store_factory=ArtifactStore,
        utc_now_iso=_utc_now_iso,
    )


def _get_broker_snapshot() -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        connected = _broker is not None and _broker.is_connected()
        info = None
        positions = []
        if connected and _broker is not None:
            info = _broker.get_account_info(timeout=_UI_BROKER_TIMEOUT_SECONDS)
            positions = _broker.get_positions(timeout=_UI_BROKER_TIMEOUT_SECONDS)
        return {
            "connected": connected,
            "info": info,
            "positions": positions,
        }

    return _get_cached_value(
        "broker-snapshot",
        _BROKER_SNAPSHOT_CACHE_TTL_SECONDS,
        _load,
        stale_on_error=True,
    )


def _get_market_browser_context() -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        connected = _broker is not None and _broker.is_connected()
        markets = []
        for ticker, info in config.MARKET_MAP.items():
            entry = {
                "ticker": ticker,
                "epic": info["epic"],
                "ig_name": info.get("ig_name", ticker),
                "status": None,
                "bid": None,
                "offer": None,
            }
            if connected and _broker is not None:
                mkt = _broker.get_market_info(
                    info["epic"],
                    timeout=_UI_BROKER_MARKET_TIMEOUT_SECONDS,
                )
                if mkt:
                    snap = mkt.get("snapshot", {})
                    entry["status"] = snap.get("marketStatus")
                    entry["bid"] = snap.get("bid")
                    entry["offer"] = snap.get("offer")
            markets.append(entry)
        return {"connected": connected, "markets": markets}

    return _get_cached_value(
        "market-browser",
        _MARKET_BROWSER_CACHE_TTL_SECONDS,
        _load,
        stale_on_error=True,
    )

def _get_research_alerts_context() -> dict[str, Any]:
    return _research_dashboard_contexts._get_research_alerts_context(
        get_cached_value=_get_cached_value,
        alerts_loader=research_dashboard.alerts,
        build_engine_a_rebalance_panel_context=_build_engine_a_rebalance_panel_context,
        relative_time_label=_relative_time_label,
        logger=logger,
    )


def _get_ledger_fragment_context() -> dict[str, Any]:
    return _fragment_context_helpers._get_ledger_fragment_context(
        get_cached_value=_get_cached_value,
        ledger_cache_ttl_seconds=_LEDGER_CACHE_TTL_SECONDS,
        get_unified_ledger_snapshot=get_unified_ledger_snapshot,
        get_ledger_reconcile_report=get_ledger_reconcile_report,
    )


def _get_risk_briefing_context() -> dict[str, Any]:
    return _fragment_context_helpers._get_risk_briefing_context(
        get_cached_value=_get_cached_value,
        risk_briefing_cache_ttl_seconds=_RISK_BRIEFING_CACHE_TTL_SECONDS,
        build_risk_briefing_payload=build_risk_briefing_payload,
    )


def _get_intelligence_feed_context() -> dict[str, Any]:
    return _fragment_context_helpers._get_intelligence_feed_context(
        get_cached_value=_get_cached_value,
        intelligence_feed_cache_ttl_seconds=_INTELLIGENCE_FEED_CACHE_TTL_SECONDS,
    )


def _get_portfolio_analytics_context(days: int) -> dict[str, Any]:
    return _fragment_context_helpers._get_portfolio_analytics_context(
        days,
        max_days=config.PORTFOLIO_ANALYTICS_MAX_DAYS,
        get_cached_value=_get_cached_value,
        portfolio_analytics_cache_ttl_seconds=_PORTFOLIO_ANALYTICS_CACHE_TTL_SECONDS,
        build_portfolio_analytics_payload=build_portfolio_analytics_payload,
    )


def _build_bookmarklet_href(js_source: str, endpoint: str) -> str:
    return _social_bookmarklet_helpers._build_bookmarklet_href(
        js_source,
        endpoint,
        re_module=_re,
    )


def _parse_debate_parts(debate_summary: str) -> list[dict[str, str]]:
    return _social_bookmarklet_helpers._parse_debate_parts(
        debate_summary,
        re_module=_re,
    )


def _extract_bookmarklet_version(js_source: str) -> str:
    return _social_bookmarklet_helpers._extract_bookmarklet_version(
        js_source,
        re_module=_re,
    )

def _build_tradingview_risk_context(
    engine_status: dict[str, Any],
    db_path: str,
) -> Optional[RiskContext]:
    return _tradingview_helpers._build_tradingview_risk_context(
        engine_status,
        db_path,
        get_tradingview_equity=lambda path: _tradingview_helpers._get_tradingview_equity(path, get_conn=get_conn),
        get_conn=get_conn,
        risk_context_cls=RiskContext,
    )


def create_app() -> FastAPI:
    return _app_factory.create_app(
        init_db=init_db,
        run_preflight_checks=_run_preflight_checks,
        config_module=config,
        control_obj=control,
        project_root=PROJECT_ROOT,
        ledger_router=ledger_router,
        advisory_router=advisory_routes.router,
        broker_router=broker_routes.router,
        webhooks_router=webhooks_routes.router,
        research_router=research_routes.router,
        fragments_router=fragments_routes.router,
        system_router=system_routes.router,
        logger_name=__name__,
    )


def _is_test_artifact_incident(item: Optional[dict[str, Any]]) -> bool:
    """Hide FastAPI TestClient-generated incidents from operator-facing UI."""
    return _operator_surface_helpers._is_test_artifact_incident(
        item,
        incident_detail_payload=_incident_detail_payload,
    )


def _incident_detail_payload(item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Parse structured incident detail payloads when present."""
    return _operator_surface_helpers._incident_detail_payload(
        item,
        json_loads=json.loads,
    )


def _is_loopback_client_ip(value: Any) -> bool:
    return _operator_surface_helpers._is_loopback_client_ip(value)


def _is_localhost_tradingview_rejection_incident(item: Optional[dict[str, Any]]) -> bool:
    """Hide local webhook rejection noise from the operator incident feed."""
    return _operator_surface_helpers._is_localhost_tradingview_rejection_incident(
        item,
        incident_detail_payload=_incident_detail_payload,
        is_loopback_client_ip=_is_loopback_client_ip,
    )


def _normalize_incident_mode(mode: str) -> str:
    return _operator_surface_helpers._normalize_incident_mode(mode)


def _incident_timestamp(item: Optional[dict[str, Any]]) -> Optional[datetime]:
    return _operator_surface_helpers._incident_timestamp(item)


def _is_active_incident(item: Optional[dict[str, Any]], *, now: Optional[datetime] = None) -> bool:
    return _operator_surface_helpers._is_active_incident(
        item,
        incident_timestamp=_incident_timestamp,
        active_incident_event_lookback=_ACTIVE_INCIDENT_EVENT_LOOKBACK,
        now=now,
    )


def _visible_incidents(limit: int = 25, mode: str = "history") -> list[dict[str, Any]]:
    """Return incidents intended for operators, excluding low-signal local noise."""
    return _operator_surface_helpers._visible_incidents(
        get_incidents=get_incidents,
        is_test_artifact_incident=_is_test_artifact_incident,
        is_localhost_tradingview_rejection_incident=_is_localhost_tradingview_rejection_incident,
        is_active_incident=_is_active_incident,
        limit=limit,
        mode=mode,
    )


def _safe_log_event(**kwargs: Any) -> None:
    """Best-effort event logging for non-critical API paths."""
    return _operator_surface_helpers._safe_log_event(log_event=log_event, **kwargs)


def build_status_payload() -> dict[str, Any]:
    return _operator_surface_helpers.build_status_payload(
        get_cached_value=_get_cached_value,
        status_cache_ttl_seconds=_STATUS_CACHE_TTL_SECONDS,
        control_status=control.status,
        get_summary=get_summary,
        get_open_option_positions=get_open_option_positions,
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
    return _operator_surface_helpers.build_risk_briefing_payload(
        calculate_fund_nav=calculate_fund_nav,
        get_risk_briefing=get_risk_briefing,
        unavailable_risk_briefing_payload=_unavailable_risk_briefing_payload,
    )


def build_portfolio_analytics_payload(days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS) -> dict[str, Any]:
    """Build portfolio analytics payload from fund daily NAV history."""
    return _operator_surface_helpers.build_portfolio_analytics_payload(
        days,
        max_days=int(config.PORTFOLIO_ANALYTICS_MAX_DAYS),
        rolling_window_default=int(config.PORTFOLIO_ANALYTICS_ROLLING_WINDOW),
        risk_free_rate=float(config.PORTFOLIO_ANALYTICS_RISK_FREE_RATE),
        get_fund_daily_reports=get_fund_daily_reports,
        compute_metrics=compute_metrics,
        compute_drawdowns=compute_drawdowns,
        compute_rolling_stats=compute_rolling_stats,
    )


def _build_research_system_state_context() -> dict[str, Any]:
    return _research_dashboard_contexts._build_research_system_state_context(
        pipeline_status=control.pipeline_status,
        research_system_active=bool(config.RESEARCH_SYSTEM_ACTIVE),
    )


def _page_context(request: Request, page_key: str, title: str) -> dict[str, Any]:
    return _operator_surface_helpers._page_context(
        request,
        page_key,
        title,
        build_status_payload=build_status_payload,
        trading_mode=config.TRADING_MODE,
        build_research_system_state_context=_build_research_system_state_context,
    )

def _tail_file(path: Path, lines: int = 200) -> str:
    return _job_helpers._tail_file(path, lines=lines)


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


def _load_order_intent_store():
    return _broker_helpers._load_order_intent_store()

def get_order_intent_items(limit: int = 50, status: str = "") -> list[dict[str, Any]]:
    return _broker_helpers.get_order_intent_items(
        limit=limit,
        status=status,
        load_order_intent_store=_load_order_intent_store,
        get_order_actions=get_order_actions,
    )


def get_order_intent_detail(intent_id: str) -> Optional[dict[str, Any]]:
    return _broker_helpers.get_order_intent_detail(
        intent_id,
        load_order_intent_store=_load_order_intent_store,
        get_order_actions=get_order_actions,
    )


def build_broker_health_payload() -> dict[str, Any]:
    return _broker_helpers.build_broker_health_payload(
        control_obj=control,
        shared_broker=_broker,
        asdict_fn=asdict,
        is_dataclass_fn=is_dataclass,
    )


def _get_editable_settings() -> dict[str, Any]:
    return _settings_helpers._get_editable_settings(config)


def _validate_settings(data: dict[str, Any]) -> list[str]:
    return _settings_helpers._validate_settings(data)


def _save_settings_overrides(data: dict[str, Any]) -> None:
    from utils.atomic_write import atomic_write_json
    _settings_helpers._save_settings_overrides(
        data,
        config_module=config,
        atomic_write_json=atomic_write_json,
    )


from app.api.routes import advisory as advisory_routes
from app.api.routes import broker as broker_routes
from app.api.routes import fragments as fragments_routes
from app.api.routes import research as research_routes
from app.api.routes import system as system_routes
from app.api.routes import webhooks as webhooks_routes

_route_compat.register_default_route_compatibility(
    __name__,
    broker_routes=broker_routes,
    fragments_routes=fragments_routes,
    research_routes=research_routes,
    webhooks_routes=webhooks_routes,
    system_routes=system_routes,
)

app = create_app()
