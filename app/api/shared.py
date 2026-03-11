"""Shared dependencies for API router modules."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import html
import ipaddress
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import re as _re


from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
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
from broker.ig import IGBroker
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    ExecutionReport,
    InstrumentSpec,
    PromotionOutcome,
    RebalanceSheet,
    RetirementMemo,
    ReviewTrigger,
    RiskLimits as ResearchRiskLimits,
    SizingSpec,
    TradeSheet,
)
from research.dashboard import ResearchDashboardService
from research.engine_b.source_scoring import SourceScoringService
from research.model_router import ModelRouter
from research.readiness import build_research_readiness_report
from research.manual_execution import (
    build_manual_engine_a_execution_report as _manual_build_engine_a_execution_report,
    build_manual_engine_a_trade_instruments as _manual_build_engine_a_trade_instruments,
    build_manual_engine_a_trade_sheet as _manual_build_engine_a_trade_sheet,
    find_chain_artifact as _manual_find_chain_artifact,
    latest_artifact_by_type as _manual_latest_artifact_by_type,
    manual_engine_a_broker_target as _manual_engine_a_broker_target,
    parse_contract_details as _manual_parse_contract_details,
    queue_manual_engine_a_order_intents as _manual_queue_manual_engine_a_order_intents,
    supersede_rebalance_sheet as _manual_supersede_rebalance_sheet,
)
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
from intelligence.sa_factor_grades import normalize_factor_grades, store_factor_grades
from intelligence.sa_quant_client import score_sa_quant_snapshot
from intelligence.scrapers.sa_adapter import (
    SA_BROWSER_CAPTURE_EVENT_TYPE,
    SA_BROWSER_CAPTURE_SOURCE,
    SA_NETWORK_CAPTURE_SOURCE,
    SA_SYMBOL_CAPTURE_EVENT_TYPE,
    normalize_sa_symbol_snapshot,
    parse_sa_browser_payload,
)
from data.pg_connection import get_pg_connection, release_pg_connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "web" / "templates"))
control = BotControlService(PROJECT_ROOT)
research = ResearchService(PROJECT_ROOT)
research_dashboard = ResearchDashboardService()
control.configure_research_services(
    engine_a_factory=lambda: build_engine_a_pipeline(),
    engine_b_factory=lambda: build_engine_b_pipeline(),
    decay_review_factory=lambda: DecayReviewService(artifact_store=ArtifactStore()),
    kill_monitor_factory=lambda: KillMonitor(artifact_store=ArtifactStore()),
)
logger = logging.getLogger(__name__)

_TRADINGVIEW_RISK_LIMITS = PreTradeRiskLimits(
    max_position_pct_equity=15.0,
    max_sleeve_pct_equity=40.0,
    max_correlated_pct_equity=60.0,
)

# ─── Shared broker session (independent of engine) ─────────────────────────
_broker: Optional[IGBroker] = None
_broker_lock = threading.Lock()
_UI_BROKER_TIMEOUT_SECONDS = 3.0
_UI_BROKER_MARKET_TIMEOUT_SECONDS = 1.5
_STATUS_CACHE_TTL_SECONDS = 2.0
_BROKER_SNAPSHOT_CACHE_TTL_SECONDS = 10.0
_BROKER_HEALTH_CACHE_TTL_SECONDS = 10.0
_MARKET_BROWSER_CACHE_TTL_SECONDS = 45.0
_RISK_BRIEFING_CACHE_TTL_SECONDS = 15.0
_INTELLIGENCE_FEED_CACHE_TTL_SECONDS = 15.0
_PORTFOLIO_ANALYTICS_CACHE_TTL_SECONDS = 30.0
_RESEARCH_CACHE_TTL_SECONDS = 15.0
_LEDGER_CACHE_TTL_SECONDS = 15.0
_ACTIVE_INCIDENT_EVENT_LOOKBACK = timedelta(minutes=60)
_EVENT_STREAM_HEARTBEAT_SECONDS = 10.0
_RESEARCH_FRAGMENT_CACHE_KEYS = (
    "research-operating-summary",
    "research-readiness",
    "research-pipeline-funnel",
    "research-active-hypotheses",
    "research-recent-decisions",
    "research-alerts",
    "research-engine-status",
)
_FRAGMENT_CACHE: dict[str, dict[str, Any]] = {}
_FRAGMENT_CACHE_LOCK = threading.Lock()
_FRAGMENT_CACHE_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_ENGINE_B_SOURCE_SCORING = SourceScoringService()
_INTEL_ANALYSIS_STALE_SECONDS = max(15 * 60, int(config.COUNCIL_ROUND_TIMEOUT) * 4)
_RESEARCH_SYNTHESIS_EVENT_TYPE = "research_synthesis"
_RESEARCH_OPERATOR_SOURCE = "research_ui"
_RESEARCH_ARCHIVE_VIEWS = {"all", "completed", "synthesis", "post_mortem", "retirement"}
_LATEST_BRIEFS: dict[str, MarketBrief] = {}  # "morning" / "evening" -> latest brief
_BRIEF_LOCK = threading.Lock()


def _get_or_create_broker() -> tuple[Optional[IGBroker], str]:
    """Return (broker, error_message). Creates and connects on first call."""
    global _broker
    with _broker_lock:
        if _broker is not None and _broker.is_connected():
            return _broker, ""

        is_demo = config.ig_broker_is_demo()
        if not config.ig_credentials_available(is_demo):
            if is_demo:
                return None, (
                    "IG demo credentials not configured. Set IG_DEMO_USERNAME, IG_DEMO_PASSWORD, "
                    "IG_DEMO_API_KEY (legacy fallback: IG_USERNAME, IG_PASSWORD, IG_API_KEY with IG_ACC_TYPE=DEMO)."
                )
            return None, (
                "IG live credentials not configured. Set IG_LIVE_USERNAME, IG_LIVE_PASSWORD, "
                "IG_LIVE_API_KEY (or legacy IG_USERNAME, IG_PASSWORD, IG_API_KEY)."
            )

        _broker = IGBroker(is_demo=is_demo)
        if _broker.connect():
            return _broker, ""

        _broker = None
        return None, "IG authentication failed — check credentials and API key"


def _run_preflight_checks(logger: logging.Logger) -> dict[str, str]:
    """Check which external services have credentials configured.

    Returns a dict of service_name -> "ok" | "missing".
    """
    checks = {
        "ig_broker": "ok" if config.ig_credentials_available(config.ig_broker_is_demo()) else "missing",
        "telegram": "ok" if (config.NOTIFICATIONS.get("telegram_token") and config.NOTIFICATIONS.get("telegram_chat_id")) else "missing",
        "anthropic_api": "ok" if os.getenv("ANTHROPIC_API_KEY") else "missing",
        "openai_api": "ok" if os.getenv("OPENAI_API_KEY") else "missing",
        "fred_api": "ok" if os.getenv("FRED_API_KEY") else "missing",
        "finnhub_api": "ok" if os.getenv("FINNHUB_API_KEY") else "missing",
        "sa_rapidapi": "ok" if os.getenv("SA_RAPIDAPI_KEY") else "missing",
        "tradingview_webhook": "ok" if config.TRADINGVIEW_WEBHOOK_TOKEN else "missing",
    }
    ok_count = sum(1 for v in checks.values() if v == "ok")
    missing = [k for k, v in checks.items() if v == "missing"]
    logger.info("Preflight: %d/%d services configured", ok_count, len(checks))
    if missing:
        logger.warning("Preflight: missing credentials for: %s", ", ".join(missing))
    return checks


from utils.datetime_utils import utc_now_iso as _utc_now_iso


def _get_cached_value(
    key: str,
    ttl_seconds: float,
    loader: Callable[[], Any],
    *,
    stale_on_error: bool = True,
) -> Any:
    now = time.monotonic()
    entry: Optional[dict[str, Any]] = None
    with _FRAGMENT_CACHE_LOCK:
        entry = _FRAGMENT_CACHE.get(key)
        if entry and now < entry["expires_at"]:
            return entry["value"]
        refresh_lock = _FRAGMENT_CACHE_REFRESH_LOCKS.setdefault(key, threading.Lock())

    if not refresh_lock.acquire(blocking=False):
        if entry is not None:
            return entry["value"]
        with refresh_lock:
            pass
        with _FRAGMENT_CACHE_LOCK:
            refreshed = _FRAGMENT_CACHE.get(key)
            if refreshed is not None:
                return refreshed["value"]
        return loader()

    try:
        value = loader()
    except Exception as exc:
        if stale_on_error and entry is not None:
            logger.warning("Using stale cached payload for %s after refresh failed: %s", key, exc)
            return entry["value"]
        raise
    finally:
        refresh_lock.release()

    with _FRAGMENT_CACHE_LOCK:
        _FRAGMENT_CACHE[key] = {
            "value": value,
            "expires_at": time.monotonic() + max(0.1, float(ttl_seconds)),
        }
        # Evict expired entries to prevent unbounded growth
        if len(_FRAGMENT_CACHE) > 50:
            expired = [k for k, v in _FRAGMENT_CACHE.items() if time.monotonic() >= v["expires_at"]]
            for k in expired:
                del _FRAGMENT_CACHE[k]
                _FRAGMENT_CACHE_REFRESH_LOCKS.pop(k, None)
    return value


def _invalidate_cached_values(*keys: str) -> None:
    with _FRAGMENT_CACHE_LOCK:
        for key in keys:
            _FRAGMENT_CACHE.pop(key, None)
            _FRAGMENT_CACHE_REFRESH_LOCKS.pop(key, None)


def _invalidate_research_cached_values() -> None:
    _invalidate_cached_values(*_RESEARCH_FRAGMENT_CACHE_KEYS)


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _relative_time_label(value: Any, *, now: datetime | None = None) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return "-"
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - parsed).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    weeks = days // 7
    return f"{weeks}w ago"


def action_message(text: str, ok: bool) -> str:
    css = "action-msg ok" if ok else "action-msg error"
    return f"<div class='{css}'>{text}</div>"


def _telegram_reply(chat_id: int, text: str) -> None:
    """Send a reply to a Telegram chat."""
    import requests as _requests
    token = os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=5,
        )
    except Exception as e:
        logger.warning("Telegram reply failed: %s", e)


def _telegram_reply_long(chat_id: int, text: str) -> None:
    """Send a long reply, splitting at Telegram's 4096 char limit."""
    if len(text) <= 4096:
        _telegram_reply(chat_id, text)
        return
    chunks = []
    while text:
        if len(text) <= 4096:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, 4096)
        if split_at < 100:
            split_at = 4096
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    for chunk in chunks:
        _telegram_reply(chat_id, chunk)
