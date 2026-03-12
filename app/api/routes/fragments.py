"""Page routes and HTMX fragment routes extracted from server.py."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

import config
from app.api import research_dashboard_contexts as _research_dashboard_contexts
from app.api import research_workbench_views as _research_workbench_views
from app.api import settings_helpers as _settings_helpers
from app.api.shared import (
    TEMPLATES,
    control,
    research,
    research_dashboard,
    logger,
    _utc_now_iso,
    _get_cached_value,
    _invalidate_cached_values,
    _invalidate_research_cached_values,
    _parse_iso_datetime,
    _relative_time_label,
    action_message,
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
    _RESEARCH_FRAGMENT_CACHE_KEYS,
    _LATEST_BRIEFS,
    _BRIEF_LOCK,
)

from app.engine.signal_shadow import get_signal_shadow_report
from data.trade_db import (
    DB_PATH,
    get_bot_events,
    get_active_strategy_parameter_set,
    get_calibration_points,
    get_calibration_runs,
    get_conn,
    get_control_actions,
    get_jobs,
    get_job,
    get_option_contract_summary,
    get_option_contracts,
    get_order_actions,
    get_trade_idea,
    get_trade_ideas,
    get_trade_ideas_by_analysis,
    get_idea_transitions,
    delete_research_events,
    delete_rejected_trade_ideas,
    get_strategy_parameter_sets,
    get_strategy_promotions,
    update_job,
)
from fund.promotion_gate import build_promotion_gate_report
from fund.execution_quality import get_execution_quality_payload
from intelligence.jobs.signal_layer_jobs import enrich_signal_shadow_payload
from intelligence.event_store import EventStore
from intelligence.scrapers.sa_adapter import SA_SYMBOL_CAPTURE_EVENT_TYPE

import re as _re

router = APIRouter(tags=["fragments"])


# ─── SA symbol capture card builder (copied from server.py, local to create_app) ──


def _build_sa_symbol_capture_cards(limit: int = 5) -> list[dict[str, Any]]:
    store = EventStore()
    events = store.list_events(limit=max(1, limit), event_type=SA_SYMBOL_CAPTURE_EVENT_TYPE)
    cards: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event, dict) else {}
        if not isinstance(payload, dict):
            continue
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        raw_fields = summary.get("raw_fields") if isinstance(summary.get("raw_fields"), dict) else {}
        normalized_sections = (
            payload.get("normalized_sections")
            if isinstance(payload.get("normalized_sections"), dict)
            else {}
        )
        section_names = sorted(str(key) for key in normalized_sections.keys())
        if not section_names and isinstance(raw_fields.get("normalized_section_names"), list):
            section_names = sorted(str(key) for key in raw_fields.get("normalized_section_names"))
        cards.append(
            {
                "id": event.get("id", ""),
                "ticker": str(summary.get("ticker") or event.get("symbol") or "").upper(),
                "captured_at": str(summary.get("captured_at") or event.get("retrieved_at") or ""),
                "retrieved_at": str(event.get("retrieved_at") or ""),
                "rating": summary.get("rating") or "",
                "quant_score": summary.get("quant_score"),
                "author_rating": summary.get("author_rating") or "",
                "wall_st_rating": summary.get("wall_st_rating") or "",
                "sector_rank": summary.get("sector_rank"),
                "industry_rank": summary.get("industry_rank"),
                "primary_price": raw_fields.get("primary_price"),
                "overall_rank": raw_fields.get("overall_rank"),
                "sector_name": raw_fields.get("sector_name") or "",
                "industry_name": raw_fields.get("industry_name") or "",
                "grades": summary.get("grades") if isinstance(summary.get("grades"), dict) else {},
                "section_names": section_names,
                "normalized_sections": normalized_sections,
                "normalized_sections_json": json.dumps(normalized_sections, indent=2, sort_keys=True),
                "version": summary.get("bookmarklet_version") or payload.get("bookmarklet_version") or "",
                "source": event.get("source", ""),
                "url": summary.get("url") or payload.get("url") or "",
            }
        )
    return cards


# ─── Settings helpers (copied from server.py, only used by settings routes) ──


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


def _build_research_queue_follow_up_context(
    selected_queue_lane: str,
    *,
    exclude_chain_id: str = "",
) -> dict[str, Any] | None:
    return _research_workbench_views._build_research_queue_follow_up_context(
        selected_queue_lane,
        exclude_chain_id=exclude_chain_id,
        get_research_alerts_context=lambda: (
            __import__("app.api.server", fromlist=["_get_research_alerts_context"])
            ._get_research_alerts_context()
        ),
    )


def _get_research_operating_summary_context() -> dict[str, Any]:
    return _research_dashboard_contexts._get_research_operating_summary_context(
        get_cached_value=lambda key, ttl_seconds, loader, stale_on_error=True: (
            __import__("app.api.server", fromlist=["_get_cached_value"])
            ._get_cached_value(key, ttl_seconds, loader, stale_on_error=stale_on_error)
        ),
        operating_summary=research_dashboard.operating_summary,
        build_engine_a_rebalance_panel_context=lambda: (
            __import__("app.api.server", fromlist=["_build_engine_a_rebalance_panel_context"])
            ._build_engine_a_rebalance_panel_context()
        ),
        build_research_queue_follow_up_context=_build_research_queue_follow_up_context,
        normalize_research_queue_lane=_research_workbench_views._normalize_research_queue_lane,
        utc_now_iso=_utc_now_iso,
        logger=logger,
    )


# ─── Page routes ─────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
def overview_page(request: Request):
    from app.api.server import _page_context
    return TEMPLATES.TemplateResponse(
        request,
        "overview.html",
        _page_context(request=request, page_key="overview", title="Overview | Trading Bot"),
    )


@router.get("/overview", response_class=HTMLResponse)
def overview_page_alias(request: Request):
    from app.api.server import _page_context
    return TEMPLATES.TemplateResponse(
        request,
        "overview.html",
        _page_context(request=request, page_key="overview", title="Overview | Trading Bot"),
    )


@router.get("/trading", response_class=HTMLResponse)
def trading_page(request: Request):
    from app.api.server import _page_context
    ctx = _page_context(request=request, page_key="trading", title="Trading | Trading Bot")
    ctx["market_map"] = config.MARKET_MAP
    # Default chart EPIC — SPY (US 500)
    spy_info = config.MARKET_MAP.get("SPY", {})
    ctx["default_epic"] = spy_info.get("epic", "IX.D.SPTRD.DAILY.IP")
    return TEMPLATES.TemplateResponse(request, "trading.html", ctx)


@router.get("/research", response_class=HTMLResponse)
def research_page(request: Request):
    from app.api.server import _page_context, _normalize_research_queue_lane, _normalize_research_active_view
    selected_chain_id = str(request.query_params.get("research_chain") or "").strip()
    selected_queue_lane = _normalize_research_queue_lane(request.query_params.get("research_lane") or "")
    selected_active_view = _normalize_research_active_view(request.query_params.get("research_view") or "")
    ctx = _page_context(request=request, page_key="research", title="Research | Trading Bot")
    ctx["research_selected_chain_id"] = selected_chain_id
    ctx["research_selected_queue_lane"] = selected_queue_lane
    ctx["research_selected_active_view"] = selected_active_view
    return TEMPLATES.TemplateResponse(
        request,
        "research_page.html",
        ctx,
    )


@router.get("/advisory", response_class=HTMLResponse)
def advisory_page(request: Request):
    from app.api.server import _page_context
    return TEMPLATES.TemplateResponse(
        request,
        "advisory_page.html",
        {
            **_page_context(request=request, page_key="advisory", title="Advisory | Trading Bot"),
            "advisor_enabled": config.ADVISOR_ENABLED,
        },
    )


@router.get("/incidents", response_class=HTMLResponse)
def incidents_page(request: Request, incident_mode: str = "active"):
    from app.api.server import _page_context, _normalize_incident_mode
    return TEMPLATES.TemplateResponse(
        request,
        "incidents_page.html",
        {
            **_page_context(request=request, page_key="incidents", title="Incidents & Jobs | Trading Bot"),
            "incident_mode": _normalize_incident_mode(incident_mode),
        },
    )


@router.get("/intel", response_class=HTMLResponse)
def intel_council_page(request: Request):
    from app.api.server import _page_context
    return TEMPLATES.TemplateResponse(
        request,
        "intel_council_page.html",
        _page_context(request=request, page_key="intel", title="Intel Council | Trading Bot"),
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    from app.api.server import _page_context
    return TEMPLATES.TemplateResponse(
        request,
        "settings_page.html",
        _page_context(request=request, page_key="settings", title="Settings | Trading Bot"),
    )


@router.get("/legacy", response_class=HTMLResponse)
def legacy_single_page(request: Request):
    from app.api.server import build_status_payload, _visible_incidents
    payload = build_status_payload()
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "page_key": "legacy",
            "title": "Legacy Dashboard | Trading Bot",
            "status": payload["engine"],
            "summary": payload["summary"],
            "open_positions": payload["open_option_positions"],
            "jobs": get_jobs(limit=20),
            "events": get_bot_events(limit=25),
            "order_actions": get_order_actions(limit=25),
            "incidents": _visible_incidents(limit=25),
            "control_actions": get_control_actions(limit=25),
            "reconcile_report": control.reconcile_report().get("report", {}),
            "option_summary": get_option_contract_summary(),
            "option_contracts": get_option_contracts(limit=40),
            "calibration_runs": get_calibration_runs(limit=20),
            "default_mode": config.TRADING_MODE,
        },
    )


# ─── Settings API ────────────────────────────────────────────────────────────


@router.get("/api/settings")
def api_get_settings():
    return _get_editable_settings()


@router.post("/api/settings", response_class=HTMLResponse)
def api_save_settings(request_body: dict[str, Any] | None = None):
    if request_body is None:
        return HTMLResponse(
            '<div class="text-red-400 text-sm py-2">No settings provided.</div>',
            status_code=422,
        )
    errors = _validate_settings(request_body)
    if errors:
        error_html = '<div class="text-red-400 text-sm py-2">' + "<br>".join(errors) + "</div>"
        return HTMLResponse(error_html, status_code=422)
    _save_settings_overrides(request_body)
    return HTMLResponse(
        '<div class="text-emerald-400 text-sm py-2">Settings saved. Restart the bot for changes to take effect.</div>'
    )


# ─── General fragment routes ─────────────────────────────────────────────────


@router.get("/fragments/top-strip", response_class=HTMLResponse)
def top_strip_fragment(request: Request):
    from app.api.server import build_status_payload, _visible_incidents
    payload = build_status_payload()
    latest = _visible_incidents(limit=1, mode="active")
    latest_incident = latest[0] if latest else None
    return TEMPLATES.TemplateResponse(
        request,
        "_top_strip.html",
        {
            "request": request,
            "status": payload["engine"],
            "latest_incident": latest_incident,
        },
    )


@router.get("/fragments/status", response_class=HTMLResponse)
def status_fragment(request: Request):
    from app.api.server import build_status_payload
    payload = build_status_payload()
    return TEMPLATES.TemplateResponse(
        request,
        "_status.html",
        {
            "request": request,
            "status": payload["engine"],
            "summary": payload["summary"],
            "open_positions": payload["open_option_positions"],
        },
    )


@router.get("/fragments/overview-engine", response_class=HTMLResponse)
def overview_engine_fragment(request: Request):
    from app.api.server import build_status_payload
    payload = build_status_payload()
    return TEMPLATES.TemplateResponse(
        request,
        "_overview_engine.html",
        {
            "request": request,
            "status": payload["engine"],
            "summary": payload["summary"],
        },
    )


@router.get("/fragments/jobs", response_class=HTMLResponse)
def jobs_fragment(request: Request):
    from app.api.server import _expire_stale_intel_analysis_jobs, _build_research_system_state_context
    _expire_stale_intel_analysis_jobs()
    return TEMPLATES.TemplateResponse(
        request,
        "_jobs.html",
        {"request": request, "jobs": get_jobs(limit=20), **_build_research_system_state_context()},
    )


@router.get("/fragments/job-detail", response_class=HTMLResponse)
def job_detail_fragment(request: Request, job_id: str = ""):
    from app.api.server import _expire_stale_intel_analysis_jobs, _parse_job_result, _build_job_detail_summary, _build_research_system_state_context
    _expire_stale_intel_analysis_jobs()
    selected_id = job_id.strip()
    if not selected_id:
        for row in get_jobs(limit=40):
            if row.get("job_type") in {
                "signal_tier1_shadow_run",
                "signal_shadow_run",
                "engine_b_manual",
                "engine_b_intake",
                "discover_options",
                "calibrate_options",
            }:
                selected_id = row.get("id", "")
                break
    item = get_job(selected_id) if selected_id else None
    parsed_result = _parse_job_result(item.get("result", "")) if item else None
    job_summary = _build_job_detail_summary(item.get("job_type", ""), parsed_result) if item else None
    return TEMPLATES.TemplateResponse(
        request,
        "_job_detail.html",
        {
            "request": request,
            "job": item,
            "parsed_result": parsed_result,
            "job_summary": job_summary,
            **_build_research_system_state_context(),
        },
    )


@router.get("/fragments/events", response_class=HTMLResponse)
def events_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_events.html",
        {"request": request, "events": get_bot_events(limit=25)},
    )


@router.get("/fragments/risk-briefing", response_class=HTMLResponse)
def risk_briefing_fragment(request: Request):
    from app.api.server import _get_risk_briefing_context
    return TEMPLATES.TemplateResponse(
        request,
        "_risk_briefing.html",
        {
            "request": request,
            "risk_briefing": _get_risk_briefing_context(),
        },
    )


@router.get("/fragments/incidents", response_class=HTMLResponse)
def incidents_fragment(request: Request, mode: str = "active"):
    from app.api.server import _visible_incidents, _normalize_incident_mode
    incident_mode = _normalize_incident_mode(mode)
    return TEMPLATES.TemplateResponse(
        request,
        "_incidents.html",
        {
            "request": request,
            "incidents": _visible_incidents(limit=25, mode=incident_mode),
            "incident_mode": incident_mode,
        },
    )


@router.get("/fragments/control-actions", response_class=HTMLResponse)
def control_actions_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_control_actions.html",
        {"request": request, "control_actions": get_control_actions(limit=25)},
    )


@router.get("/fragments/intelligence-feed", response_class=HTMLResponse)
def intelligence_feed_fragment(request: Request):
    from app.api.server import _get_intelligence_feed_context
    context = _get_intelligence_feed_context()

    return TEMPLATES.TemplateResponse(
        request,
        "_intelligence_feed.html",
        {
            "request": request,
            **context,
        },
    )


@router.get("/fragments/sa-symbol-captures", response_class=HTMLResponse)
def sa_symbol_captures_fragment(request: Request, limit: int = 5):
    cards = _build_sa_symbol_capture_cards(limit=min(max(limit, 1), 10))
    return TEMPLATES.TemplateResponse(
        request,
        "_sa_symbol_captures.html",
        {
            "request": request,
            "captures": cards,
        },
    )


@router.get("/fragments/intel-next-action", response_class=HTMLResponse)
def intel_next_action_fragment(request: Request):
    """Render the Intel 'what to do next' action bar."""
    # Check for active council jobs
    active_jobs: list[dict] = []
    try:
        conn = get_conn(DB_PATH)
        running = conn.execute(
            """SELECT id FROM jobs
               WHERE job_type = 'intel_analysis' AND status IN ('queued', 'running')
               ORDER BY created_at DESC LIMIT 5"""
        ).fetchall()
        conn.close()
        active_jobs = [{"id": r[0]} for r in running]
    except Exception:
        pass

    # Get pipeline stage counts
    all_ideas = get_trade_ideas(limit=500)
    stage_counts: dict[str, int] = {}
    for idea in all_ideas:
        stage = idea.get("pipeline_stage", "idea")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    idea_count = stage_counts.get("idea", 0)
    advancing = sum(stage_counts.get(s, 0) for s in ("review", "backtest", "paper"))

    # Check for recent analyses
    recent_count = 0
    try:
        es = EventStore()
        recent_count = len(es.list_events(limit=5, event_type="intel_analysis"))
    except Exception:
        pass

    # Determine state in priority order
    if active_jobs:
        n = len(active_jobs)
        state = "running"
        message = f"Analysis in progress \u2014 {n} job{'s' if n != 1 else ''} active"
        action_label = "View Progress"
        action_target = "#intel-council-panel"
        action_type = "scroll"
    elif idea_count > 0:
        state = "review"
        message = f"{idea_count} idea{'s' if idea_count != 1 else ''} ready for review"
        action_label = "Review Ideas"
        action_target = "#idea-pipeline-board"
        action_type = "scroll"
    elif recent_count > 0 and not all_ideas:
        state = "complete"
        message = "Latest analysis complete \u2014 review results below"
        action_label = "See Results"
        action_target = "#intel-council-panel"
        action_type = "scroll"
    elif advancing > 0:
        state = "advancing"
        message = f"{advancing} idea{'s' if advancing != 1 else ''} advancing through pipeline"
        action_label = "View Pipeline"
        action_target = "#idea-pipeline-board"
        action_type = "scroll"
    else:
        state = "idle"
        message = "Nothing queued. Paste text or a link above to start."
        action_label = "Start Here"
        action_target = "textarea[name=content]"
        action_type = "focus"

    return TEMPLATES.TemplateResponse(
        request,
        "_intel_next_action.html",
        {
            "request": request,
            "state": state,
            "message": message,
            "action_label": action_label,
            "action_target": action_target,
            "action_type": action_type,
        },
    )


@router.get("/fragments/intel-council", response_class=HTMLResponse)
def intel_council_fragment(request: Request):
    """Render the LLM council analysis feed."""
    from app.api.server import _expire_stale_intel_analysis_jobs, _parse_debate_parts
    _expire_stale_intel_analysis_jobs()

    # Check for active council jobs
    active_jobs = []
    try:
        conn = get_conn(DB_PATH)
        running = conn.execute(
            """SELECT id, status, detail, created_at FROM jobs
               WHERE job_type = 'intel_analysis' AND status IN ('queued', 'running')
               ORDER BY created_at DESC LIMIT 5"""
        ).fetchall()
        conn.close()
        now = datetime.now(timezone.utc)
        for r in running:
            created = r[3] or ""
            elapsed = ""
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    secs = (now - dt).total_seconds()
                    elapsed = f"{int(secs)}s" if secs < 120 else f"{int(secs/60)}m{int(secs%60)}s"
                except Exception:
                    pass
            # Check which models have completed for this analysis
            models_done = set()
            analysis_round = 1
            try:
                conn2 = get_conn(DB_PATH)
                # Match by timestamp proximity (within 5 min of job creation)
                cost_rows = conn2.execute(
                    """SELECT model, round FROM council_costs
                       WHERE timestamp > datetime(?, '-5 minutes')
                       ORDER BY timestamp""",
                    (created,),
                ).fetchall()
                conn2.close()
                for cr in cost_rows:
                    base = cr[0].split("(")[0].strip().split()[0].lower()
                    models_done.add(base)
                    if cr[1] > analysis_round:
                        analysis_round = cr[1]
            except Exception:
                pass
            active_jobs.append({
                "id": r[0], "status": r[1],
                "detail": (r[2] or "")[:120],
                "elapsed": elapsed,
                "models_done": models_done,
                "round": analysis_round,
            })
    except Exception:
        pass

    es = EventStore()
    events = es.list_events(limit=30, event_type="intel_analysis")

    analyses = []
    for ev in events:
        payload = ev.get("payload", {}) if isinstance(ev, dict) else {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        # Parse per-model summaries from the combined summary string
        summary_raw = payload.get("summary", "")
        summary_parts = []
        if summary_raw:
            parts = _re.split(r'\[(\w+)\]\s*', summary_raw)
            # parts = ['', 'claude', 'text...', 'chatgpt', 'text...', ...]
            i = 1
            while i < len(parts) - 1:
                summary_parts.append({"model": parts[i], "text": parts[i + 1].strip()})
                i += 2
            if not summary_parts and summary_raw:
                summary_parts.append({"model": "combined", "text": summary_raw})

        # Enrich trade ideas with DB record IDs for pipeline actions
        analysis_id = payload.get("analysis_id", ev.get("id", ""))
        raw_ideas = payload.get("trade_ideas", [])
        db_ideas = get_trade_ideas_by_analysis(analysis_id)
        # Match DB ideas to raw ideas by ticker+direction
        db_lookup = {}
        for di in db_ideas:
            key = (di.get("ticker", "").upper(), di.get("direction", ""))
            db_lookup[key] = di
        enriched_ideas = []
        for ri in raw_ideas:
            key = ((ri.get("ticker") or "").upper(), ri.get("direction", ""))
            db_rec = db_lookup.get(key)
            idea_out = dict(ri)
            if db_rec:
                idea_out["idea_id"] = db_rec["id"]
                idea_out["pipeline_stage"] = db_rec.get("pipeline_stage", "idea")
            enriched_ideas.append(idea_out)

        analyses.append({
            "analysis_id": analysis_id,
            "analyzed_at": payload.get("analyzed_at", ev.get("created_at", "")),
            "source": payload.get("source", ev.get("source", "")),
            "title": payload.get("title", ev.get("headline", "")),
            "url": payload.get("url", ""),
            "confidence": payload.get("confidence", ev.get("confidence", 0)) or 0,
            "models_used": payload.get("models_used", 0),
            "summary_parts": summary_parts,
            "trade_ideas": enriched_ideas,
            "risk_factors": payload.get("risk_factors", []),
            "tickers_identified": payload.get("tickers_identified", []),
            "debate_summary": payload.get("debate_summary", ""),
            "debate_parts": _parse_debate_parts(payload.get("debate_summary", "")),
        })

    return TEMPLATES.TemplateResponse(
        request,
        "_intel_council.html",
        {"request": request, "analyses": analyses, "active_jobs": active_jobs},
    )


@router.get("/fragments/intel-costs", response_class=HTMLResponse)
def intel_costs_fragment(request: Request):
    """Render council cost monitoring panel."""
    from intelligence.intel_pipeline import get_council_cost_summary
    costs = get_council_cost_summary()
    return TEMPLATES.TemplateResponse(
        request,
        "_intel_costs.html",
        {"request": request, "costs": costs},
    )


@router.get("/fragments/intel-pipeline-summary", response_class=HTMLResponse)
def intel_pipeline_summary_fragment(request: Request):
    """Render pipeline summary sidebar with real DB stage counts."""

    # Get real stage counts from trade_ideas table
    all_db_ideas = get_trade_ideas(limit=500)
    stage_counts = {"idea": 0, "review": 0, "backtest": 0, "paper": 0, "live": 0}
    ticker_set = set()
    total_conf = 0.0
    for idea in all_db_ideas:
        stage = idea.get("pipeline_stage", "idea")
        if stage in stage_counts:
            stage_counts[stage] += 1
        if idea.get("ticker"):
            ticker_set.add(idea["ticker"])
        total_conf += idea.get("confidence", 0) or 0

    stages = [{"name": k, "count": v} for k, v in stage_counts.items()]

    # Top ideas sorted by confidence
    top_ideas = sorted(all_db_ideas, key=lambda x: x.get("confidence", 0), reverse=True)[:6]

    # Fallback: if no DB ideas yet, count from events
    total_analyses = 0
    if not all_db_ideas:
        try:
            es = EventStore()
            events = es.list_events(limit=100, event_type="intel_analysis")
            total_analyses = len(events)
            for ev in events:
                payload = ev.get("payload", {}) if isinstance(ev, dict) else {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                for idea in payload.get("trade_ideas", []):
                    if idea.get("ticker"):
                        ticker_set.add(idea["ticker"])
                        stage_counts["idea"] += 1
            stages = [{"name": k, "count": v} for k, v in stage_counts.items()]
        except Exception:
            pass

    return TEMPLATES.TemplateResponse(
        request,
        "_intel_pipeline_summary.html",
        {
            "request": request,
            "stages": stages,
            "top_ideas": top_ideas,
            "total_analyses": total_analyses or len(all_db_ideas),
            "total_ideas": len(all_db_ideas) or sum(s["count"] for s in stages),
            "avg_confidence": (total_conf / len(all_db_ideas)) if all_db_ideas else 0,
            "unique_tickers": len(ticker_set),
        },
    )


@router.get("/fragments/signal-engine", response_class=HTMLResponse)
def signal_engine_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_signal_engine.html",
        {
            "request": request,
            "signal_shadow": enrich_signal_shadow_payload(get_signal_shadow_report()),
        },
    )


@router.get("/fragments/execution-quality", response_class=HTMLResponse)
def execution_quality_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_execution_quality.html",
        {
            "request": request,
            "eq": get_execution_quality_payload(days=30),
        },
    )


@router.get("/fragments/portfolio-analytics", response_class=HTMLResponse)
def portfolio_analytics_fragment(request: Request, days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS):
    from app.api.server import _get_portfolio_analytics_context
    return TEMPLATES.TemplateResponse(
        request,
        "_portfolio_analytics.html",
        {
            "request": request,
            "analytics": _get_portfolio_analytics_context(days=days),
        },
    )


@router.get("/fragments/promotion-gate", response_class=HTMLResponse)
def promotion_gate_fragment(
    request: Request,
    strategy_key: str = config.DEFAULT_STRATEGY_KEY,
    cooldown_hours: int = 24,
):
    return TEMPLATES.TemplateResponse(
        request,
        "_promotion_gate.html",
        {
            "request": request,
            "report": build_promotion_gate_report(
                strategy_key=strategy_key,
                cooldown_hours=cooldown_hours,
            ),
        },
    )


@router.get("/fragments/calibration-run", response_class=HTMLResponse)
def calibration_run_fragment(
    request: Request,
    run_id: str = "",
    index_name: str = "",
    ticker: str = "",
    expiry_type: str = "",
    strike_min: Optional[float] = None,
    strike_max: Optional[float] = None,
    limit: int = 200,
):
    selected_run_id = run_id.strip()
    if not selected_run_id:
        latest = get_calibration_runs(limit=1)
        selected_run_id = latest[0]["id"] if latest else ""

    points = []
    if selected_run_id:
        points = get_calibration_points(
            run_id=selected_run_id,
            limit=limit,
            index_name=index_name or None,
            ticker=ticker or None,
            expiry_type=expiry_type or None,
            strike_min=strike_min,
            strike_max=strike_max,
        )

    ratios = [
        float(p["ratio_ig_vs_bs"])
        for p in points
        if p.get("ratio_ig_vs_bs") is not None
    ]
    summary = {
        "count": len(points),
        "avg_ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "min_ratio": min(ratios) if ratios else None,
        "max_ratio": max(ratios) if ratios else None,
    }
    return TEMPLATES.TemplateResponse(
        request,
        "_calibration_run_detail.html",
        {
            "request": request,
            "run_id": selected_run_id,
            "points": points,
            "summary": summary,
            "filters": {
                "index_name": index_name,
                "ticker": ticker,
                "expiry_type": expiry_type,
                "strike_min": strike_min,
                "strike_max": strike_max,
                "limit": limit,
            },
        },
    )


@router.get("/fragments/log-tail", response_class=HTMLResponse)
def log_tail_fragment(request: Request):
    from app.api.server import _tail_file
    try:
        log_text = _tail_file(control.process_log, lines=120)
    except FileNotFoundError:
        log_text = ""
    return TEMPLATES.TemplateResponse(
        request,
        "_log_tail.html",
        {"request": request, "log_text": log_text},
    )


@router.get("/fragments/backtest", response_class=HTMLResponse)
def backtest_fragment(request: Request):
    """Fragment showing recent backtest runs."""
    from app.api.server import _parse_job_result
    backtest_jobs = [
        j for j in get_jobs(limit=20)
        if j.get("job_type") == "backtest"
    ]
    for j in backtest_jobs:
        j["result_parsed"] = _parse_job_result(j.get("result") or "")
    return TEMPLATES.TemplateResponse(
        request,
        "_backtest.html",
        {"request": request, "backtest_jobs": backtest_jobs},
    )


@router.get("/fragments/market-brief", response_class=HTMLResponse)
def market_brief_fragment(request: Request):
    """Render the latest market brief."""
    with _BRIEF_LOCK:
        # Show the most recent brief (morning or evening)
        brief = _LATEST_BRIEFS.get("morning") or _LATEST_BRIEFS.get("evening")
    return TEMPLATES.TemplateResponse(
        request,
        "_market_brief.html",
        {"request": request, "brief": brief.to_dict() if brief else None},
    )


@router.get("/fragments/pipeline-status", response_class=HTMLResponse)
def pipeline_status_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_pipeline_status.html",
        {"request": request, "pipeline": control.pipeline_status()},
    )


@router.get("/fragments/idea-actions/{idea_id}", response_class=HTMLResponse)
def idea_actions_fragment(idea_id: str, request: Request):
    """Render stage-appropriate action buttons for an idea."""
    from data.trade_db import get_research_steps
    idea = get_trade_idea(idea_id)
    if not idea:
        return HTMLResponse('<span class="text-[10px] text-red-400">Idea not found</span>')

    stage = idea.get("pipeline_stage", "idea")
    bt_result = None
    if idea.get("backtest_result_json"):
        try:
            bt_result = json.loads(idea["backtest_result_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    research_steps = get_research_steps(idea_id)

    return TEMPLATES.TemplateResponse(
        request,
        "_idea_actions.html",
        {"request": request, "idea": idea, "stage": stage,
         "bt_result": bt_result, "research_steps": research_steps},
    )


@router.get("/fragments/idea-detail/{idea_id}", response_class=HTMLResponse)
def idea_detail_fragment(idea_id: str, request: Request):
    """Full idea detail card with backtest, paper, timeline."""
    idea = get_trade_idea(idea_id)
    if not idea:
        return HTMLResponse('<span class="text-[10px] text-red-400">Idea not found</span>')

    transitions = get_idea_transitions(idea_id)
    bt_result = None
    if idea.get("backtest_result_json"):
        try:
            bt_result = json.loads(idea["backtest_result_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    paper_status = None
    if idea.get("paper_deal_id"):
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        paper_status = mgr.get_paper_trade_status(idea_id)

    return TEMPLATES.TemplateResponse(
        request,
        "_idea_detail.html",
        {
            "request": request,
            "idea": idea,
            "transitions": transitions,
            "bt_result": bt_result,
            "paper_status": paper_status,
        },
    )


@router.get("/fragments/idea-pipeline-board", response_class=HTMLResponse)
def idea_pipeline_board_fragment(request: Request):
    """Kanban-style board showing ideas grouped by stage."""
    from intelligence.idea_pipeline import IdeaPipelineManager, STAGES
    mgr = IdeaPipelineManager()
    stats = mgr.get_pipeline_stats()
    return TEMPLATES.TemplateResponse(
        request,
        "_idea_pipeline_board.html",
        {"request": request, "stats": stats, "stages": STAGES},
    )


# ─── Clear / archive endpoints ────────────────────────────────────────────────


@router.post("/api/intel/clear-feed", response_class=JSONResponse)
def clear_intel_council_feed(request: Request):
    """Delete all intel_analysis events from the council feed."""
    deleted = delete_research_events(event_type="intel_analysis")
    return JSONResponse({"ok": True, "deleted": deleted})


@router.post("/api/ideas/clear-rejected", response_class=JSONResponse)
def clear_rejected_ideas(request: Request):
    """Delete all rejected trade ideas."""
    deleted = delete_rejected_trade_ideas()
    return JSONResponse({"ok": True, "deleted": deleted})


# ─── SSE stream ──────────────────────────────────────────────────────────────


@router.get("/api/stream/events")
async def events_stream(request: Request):
    async def event_generator():
        last_id = None
        last_heartbeat = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    break

                latest = get_bot_events(limit=1)
                if latest:
                    event = latest[0]
                    event_id = event.get("id")
                    if event_id != last_id:
                        last_id = event_id
                        payload = json.dumps(event)
                        yield f"event: bot_event\ndata: {payload}\n\n"
                        last_heartbeat = time.monotonic()
                elif time.monotonic() - last_heartbeat >= _EVENT_STREAM_HEARTBEAT_SECONDS:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()

                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Research-related fragment routes ─────────────────────────────────────────


@router.get("/fragments/research", response_class=HTMLResponse)
def research_fragment(request: Request, queue_lane: str = "all", chain_id: str = "", active_view: str = "all"):
    context = _research_dashboard_contexts._get_research_fragment_context(
        get_cached_value=_get_cached_value,
        research_cache_ttl_seconds=_RESEARCH_CACHE_TTL_SECONDS,
        get_calibration_runs=get_calibration_runs,
        get_option_contract_summary=get_option_contract_summary,
        get_option_contracts=get_option_contracts,
        get_strategy_parameter_sets=get_strategy_parameter_sets,
        get_strategy_promotions=get_strategy_promotions,
        get_active_strategy_parameter_set=get_active_strategy_parameter_set,
        build_promotion_gate_report=build_promotion_gate_report,
        default_strategy_key=config.DEFAULT_STRATEGY_KEY,
    )
    return TEMPLATES.TemplateResponse(
        request,
        "_research.html",
        {
            "request": request,
            "selected_queue_lane": _research_workbench_views._normalize_research_queue_lane(queue_lane),
            "selected_chain_id": str(chain_id or "").strip(),
            "selected_active_view": _research_workbench_views._normalize_research_active_view(active_view),
            **context,
        },
    )


@router.get("/fragments/research/artifact-chain", response_class=HTMLResponse)
def research_artifact_chain_placeholder_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_artifact_chain.html",
        {
            "request": request,
            "chain_id": "",
            "artifacts": [],
            "artifact_count": 0,
            "latest": None,
            "error": "",
            "generated_at": _utc_now_iso(),
        },
    )


@router.get("/fragments/research/artifact-chain/{chain_id}", response_class=HTMLResponse)
def research_artifact_chain_fragment(request: Request, chain_id: str):
    from app.api.server import _build_research_artifact_chain_context
    return TEMPLATES.TemplateResponse(
        request,
        "_research_artifact_chain.html",
        {
            "request": request,
            **_build_research_artifact_chain_context(chain_id),
        },
    )


@router.get("/fragments/research/operator-output", response_class=HTMLResponse)
def research_operator_output_fragment(request: Request, chain_id: str = "", queue_lane: str = "all", active_view: str = "all"):
    from app.api.routes import research as research_routes
    return research_routes._render_research_operator_output(
        request,
        chain_id=chain_id,
        queue_lane=_research_workbench_views._normalize_research_queue_lane(queue_lane),
        active_view=_research_workbench_views._normalize_research_active_view(active_view),
    )


@router.get("/fragments/research/focus-ribbon", response_class=HTMLResponse)
def research_focus_ribbon_fragment(
    request: Request,
    chain_id: str = "",
    queue_lane: str = "all",
    active_view: str = "all",
    suppress_auto_sync: bool = False,
):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_focus_ribbon.html",
        {
            "request": request,
            **_research_workbench_views._build_research_focus_ribbon_context(
                chain_id=chain_id,
                queue_lane=_research_workbench_views._normalize_research_queue_lane(queue_lane),
                active_view=_research_workbench_views._normalize_research_active_view(active_view),
                suppress_auto_sync=bool(suppress_auto_sync),
                artifact_store_factory=lambda: __import__("app.api.server", fromlist=["ArtifactStore"]).ArtifactStore(),
                build_research_artifact_chain_context=lambda active_chain_id, artifact_store=None: (
                    __import__("app.api.server", fromlist=["_build_research_artifact_chain_context"])
                    ._build_research_artifact_chain_context(active_chain_id, artifact_store=artifact_store)
                ),
                build_research_operating_summary_context=_get_research_operating_summary_context,
                build_research_queue_follow_up_context=_build_research_queue_follow_up_context,
                utc_now_iso=_utc_now_iso,
            ),
        },
    )


@router.get("/fragments/research/archive", response_class=HTMLResponse)
def research_archive_fragment(
    request: Request,
    limit: int = 6,
    ticker: str = "",
    q: str = "",
    view: str = "all",
):
    from app.api.server import _build_research_archive_context
    return TEMPLATES.TemplateResponse(
        request,
        "_research_archive.html",
        {
            "request": request,
            **_build_research_archive_context(
                limit=max(1, min(limit, 20)),
                ticker=ticker,
                search_text=q,
                view=view,
            ),
        },
    )


@router.get("/fragments/research/regime-panel", response_class=HTMLResponse)
def research_regime_panel_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_regime_panel.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_regime_panel_context(
                get_cached_value=_get_cached_value,
                build_engine_a_regime_panel_context=lambda: __import__("app.api.server", fromlist=["_build_engine_a_regime_panel_context"])._build_engine_a_regime_panel_context(),
            ),
        },
    )


@router.get("/fragments/research/signal-heatmap", response_class=HTMLResponse)
def research_signal_heatmap_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_signal_heatmap.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_signal_heatmap_context(
                get_cached_value=_get_cached_value,
                build_engine_a_signal_heatmap_context=lambda: __import__("app.api.server", fromlist=["_build_engine_a_signal_heatmap_context"])._build_engine_a_signal_heatmap_context(),
            ),
        },
    )


@router.get("/fragments/research/portfolio-targets", response_class=HTMLResponse)
def research_portfolio_targets_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_portfolio_targets.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_portfolio_targets_context(
                get_cached_value=_get_cached_value,
                build_engine_a_portfolio_targets_context=lambda: __import__("app.api.server", fromlist=["_build_engine_a_portfolio_targets_context"])._build_engine_a_portfolio_targets_context(),
            ),
        },
    )


@router.get("/fragments/research/rebalance-panel", response_class=HTMLResponse)
def research_rebalance_panel_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_rebalance_panel.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_rebalance_panel_context(
                get_cached_value=_get_cached_value,
                build_engine_a_rebalance_panel_context=lambda: __import__("app.api.server", fromlist=["_build_engine_a_rebalance_panel_context"])._build_engine_a_rebalance_panel_context(),
            ),
        },
    )


@router.get("/fragments/research/regime-journal", response_class=HTMLResponse)
def research_regime_journal_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_regime_journal.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_regime_journal_context(
                get_cached_value=_get_cached_value,
                build_engine_a_regime_journal_context=lambda: __import__("app.api.server", fromlist=["_build_engine_a_regime_journal_context"])._build_engine_a_regime_journal_context(),
            ),
        },
    )


@router.get("/fragments/research/operating-summary", response_class=HTMLResponse)
def research_operating_summary_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_operating_summary.html",
        {"request": request, **_get_research_operating_summary_context()},
    )


@router.get("/fragments/research-next-action", response_class=HTMLResponse)
def research_next_action_fragment(request: Request):
    """Render the Research 'what to do next' action bar."""
    ctx = _get_research_operating_summary_context()

    state = ctx.get("focus_tone", "idle")
    title = ctx.get("focus_title", "No active research")
    detail = ctx.get("focus_detail", "")
    anchor = ctx.get("focus_anchor", "#research-intake")

    return TEMPLATES.TemplateResponse(
        request,
        "_research_next_action.html",
        {
            "request": request,
            "state": state,
            "title": title,
            "detail": detail,
            "anchor": anchor,
        },
    )


@router.get("/fragments/research/pipeline-funnel", response_class=HTMLResponse)
def research_pipeline_funnel_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_pipeline_funnel.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_pipeline_funnel_context(
                get_cached_value=_get_cached_value,
                pipeline_funnel=research_dashboard.pipeline_funnel,
                logger=logger,
            ),
        },
    )


@router.get("/fragments/research/readiness", response_class=HTMLResponse)
def research_readiness_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_readiness.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_readiness_context(
                get_cached_value=_get_cached_value,
                build_research_readiness_report=__import__("research.readiness", fromlist=["build_research_readiness_report"]).build_research_readiness_report,
                pipeline_status=control.pipeline_status,
                utc_now_iso=_utc_now_iso,
                research_system_active=bool(getattr(config, "RESEARCH_SYSTEM_ACTIVE", False)),
                logger=logger,
            ),
        },
    )


@router.get("/fragments/research/active-hypotheses", response_class=HTMLResponse)
def research_active_hypotheses_fragment(request: Request, active_view: str = "all", chain_id: str = ""):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_active_hypotheses.html",
        {
            "request": request,
            "selected_active_view": _research_workbench_views._normalize_research_active_view(active_view),
            "selected_chain_id": str(chain_id or "").strip(),
            **_research_dashboard_contexts._get_research_active_hypotheses_context(
                get_cached_value=_get_cached_value,
                active_hypotheses=research_dashboard.active_hypotheses,
                logger=logger,
            ),
        },
    )


@router.get("/fragments/research/engine-status", response_class=HTMLResponse)
def research_engine_status_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_engine_status.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_engine_status_context(
                get_cached_value=_get_cached_value,
                pipeline_status=control.pipeline_status,
                pipeline_funnel=research_dashboard.pipeline_funnel,
                utc_now_iso=_utc_now_iso,
                logger=logger,
            ),
        },
    )


@router.get("/fragments/research/recent-decisions", response_class=HTMLResponse)
def research_recent_decisions_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_research_recent_decisions.html",
        {
            "request": request,
            **_research_dashboard_contexts._get_research_recent_decisions_context(
                get_cached_value=_get_cached_value,
                recent_decisions=research_dashboard.recent_decisions,
                logger=logger,
            ),
        },
    )


@router.get("/fragments/research/alerts", response_class=HTMLResponse)
def research_alerts_fragment(request: Request, queue_lane: str = "all", chain_id: str = ""):
    clean_chain_id = str(chain_id or "").strip()
    normalized_queue_lane = _research_workbench_views._normalize_research_queue_lane(queue_lane)
    alerts_context = __import__("app.api.server", fromlist=["_get_research_alerts_context"])._get_research_alerts_context()
    return TEMPLATES.TemplateResponse(
        request,
        "_research_alerts.html",
        {
            "request": request,
            "selected_queue_lane": normalized_queue_lane,
            "selected_chain_id": clean_chain_id,
            "selected_chain_context": _research_workbench_views._build_research_selected_chain_queue_context(
                clean_chain_id,
                build_research_artifact_chain_context=lambda active_chain_id, artifact_store=None: (
                    __import__("app.api.server", fromlist=["_build_research_artifact_chain_context"])
                    ._build_research_artifact_chain_context(active_chain_id, artifact_store=artifact_store)
                ),
                logger=logger,
            ),
            "next_queue_item": (
                _research_workbench_views._build_research_next_queue_item_context(
                    normalized_queue_lane,
                    alerts=alerts_context,
                    exclude_chain_id=clean_chain_id,
                )
                if not clean_chain_id
                else None
            ),
            **alerts_context,
        },
    )
