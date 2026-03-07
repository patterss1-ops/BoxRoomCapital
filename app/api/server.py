"""FastAPI app for bot control and monitoring."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import html
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

import re as _re

import requests as _requests

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
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
    enrich_signal_shadow_payload,
    run_tier1_shadow_jobs,
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
from intelligence.event_store import EventRecord, EventStore, compute_event_id
from intelligence.feature_store import FeatureStore
from risk.pre_trade_gate import RiskContext, RiskLimits, RiskOrderRequest, evaluate_pre_trade_risk
from risk.portfolio_risk import get_risk_briefing
from intelligence.intel_pipeline import (
    IntelSubmission,
    analyze_intel_async,
)
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "web" / "templates"))
control = BotControlService(PROJECT_ROOT)
research = ResearchService(PROJECT_ROOT)
logger = logging.getLogger(__name__)

_TRADINGVIEW_RISK_LIMITS = RiskLimits(
    max_position_pct_equity=15.0,
    max_sleeve_pct_equity=40.0,
    max_correlated_pct_equity=60.0,
)

# ─── Shared broker session (independent of engine) ─────────────────────────
_broker: Optional[IGBroker] = None


def _get_or_create_broker() -> tuple[Optional[IGBroker], str]:
    """Return (broker, error_message). Creates and connects on first call."""
    global _broker
    if _broker is not None and _broker.is_connected():
        return _broker, ""

    if not (config.IG_USERNAME and config.IG_PASSWORD and config.IG_API_KEY):
        return None, "IG credentials not configured (IG_USERNAME, IG_PASSWORD, IG_API_KEY)"

    _broker = IGBroker(is_demo=(config.IG_ACC_TYPE == "DEMO"))
    if _broker.connect():
        return _broker, ""

    _broker = None
    return None, "IG authentication failed — check credentials and API key"


def _run_preflight_checks(logger: logging.Logger) -> dict[str, str]:
    """Check which external services have credentials configured.

    Returns a dict of service_name -> "ok" | "missing".
    """
    checks = {
        "ig_broker": "ok" if (config.IG_USERNAME and config.IG_PASSWORD and config.IG_API_KEY) else "missing",
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_bookmarklet_href(js_source: str, endpoint: str) -> str:
    """Build a safe bookmarklet href without corrupting embedded URLs."""
    src = js_source.replace("%%ENDPOINT%%", endpoint)
    src = _re.sub(r"/\*.*?\*/", "", src, flags=_re.DOTALL)
    src = _re.sub(r"(?m)^\s*//.*$", "", src)
    src = _re.sub(r"\s+", " ", src).strip()
    return "javascript:" + src


def _parse_debate_parts(debate_summary: str) -> list[dict[str, str]]:
    """Parse the debate summary into per-model parts for display."""
    if not debate_summary:
        return []
    parts = _re.split(r'\[(\w+)\]\s*', debate_summary)
    result = []
    i = 1
    while i < len(parts) - 1:
        result.append({"model": parts[i], "text": parts[i + 1].strip()})
        i += 2
    if not result and debate_summary:
        result.append({"model": "council", "text": debate_summary})
    return result


def _extract_bookmarklet_version(js_source: str) -> str:
    """Extract the inline bookmarklet version stamp when present."""
    match = _re.search(r'BOOKMARKLET_VERSION\s*=\s*"([^"]+)"', js_source)
    if not match:
        return "unknown"
    return match.group(1).strip() or "unknown"


def _tradingview_event_descriptor(alert: NormalizedTradingViewAlert) -> dict[str, Any]:
    return {
        "provider": "tradingview",
        "strategy_id": alert.strategy_id,
        "ticker": alert.ticker,
        "action": alert.action,
        "timeframe": alert.timeframe,
        "alert_id": alert.alert_id,
        "event_timestamp": alert.event_timestamp,
    }


def _tradingview_event_id(alert: NormalizedTradingViewAlert) -> str:
    return compute_event_id(
        event_type="signal",
        source="tradingview",
        descriptor=_tradingview_event_descriptor(alert),
        source_ref=alert.source_ref,
    )


def _decode_json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_tradingview_lane(
    strategy_id: str,
    db_path: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    for lane in ("live", "staged_live", "shadow"):
        active = get_active_strategy_parameter_set(strategy_id, status=lane, db_path=db_path)
        if active:
            return lane, active
    return "missing", None


def _tradingview_action_semantics(action: str) -> tuple[str, bool]:
    clean = str(action or "").strip().lower()
    mapping = {
        "buy": (OrderSide.BUY.value, False),
        "sell": (OrderSide.SELL.value, True),
        "short": (OrderSide.SELL.value, False),
        "cover": (OrderSide.BUY.value, True),
    }
    result = mapping.get(clean)
    if result is None:
        raise ValueError(f"Unsupported TradingView action '{action}'")
    return result


def _build_tradingview_route_state(engine_status: dict[str, Any]) -> RoutePolicyState:
    cooldowns = engine_status.get("cooldowns") or {}
    cooldown_tickers = {
        str(ticker).upper()
        for ticker in cooldowns.keys()
        if str(ticker).strip()
    }
    return RoutePolicyState(
        kill_switch_active=bool(engine_status.get("kill_switch_active")),
        kill_switch_reason=str(engine_status.get("kill_switch_reason") or ""),
        cooldown_tickers=cooldown_tickers,
    )


def _build_tradingview_router(spec: TradingViewStrategySpec) -> AccountRouter:
    broker_name = str(spec.broker_target or "").strip().lower()
    return AccountRouter(
        route_map={
            f"strategy:{spec.strategy_id}": RouteConfigEntry(
                broker_name=broker_name,
                account_type=RouteAccountType(spec.account_type),
            ),
        },
        brokers={broker_name: default_broker_resolver(broker_name)},
    )


def _get_tradingview_equity(db_path: str) -> float:
    try:
        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT total_nav FROM fund_daily_report ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return 0.0
    if not row:
        return 0.0
    try:
        return float(row["total_nav"] or 0.0)
    except (TypeError, ValueError, KeyError):
        return 0.0


def _build_tradingview_risk_context(
    engine_status: dict[str, Any],
    db_path: str,
) -> Optional[RiskContext]:
    equity = _get_tradingview_equity(db_path)
    if equity <= 0:
        return None

    conn = get_conn(db_path)
    ticker_rows = conn.execute(
        """SELECT UPPER(bp.ticker) as ticker,
                  SUM(ABS(CAST(bp.market_value AS REAL))) as exposure
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1
           GROUP BY UPPER(bp.ticker)"""
    ).fetchall()
    sleeve_rows = conn.execute(
        """SELECT COALESCE(bp.sleeve, 'unassigned') as sleeve,
                  SUM(ABS(CAST(bp.market_value AS REAL))) as exposure
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1
           GROUP BY COALESCE(bp.sleeve, 'unassigned')"""
    ).fetchall()
    conn.close()

    cooldowns = engine_status.get("cooldowns") or {}
    return RiskContext(
        equity=equity,
        kill_switch_active=bool(engine_status.get("kill_switch_active")),
        kill_switch_reason=str(engine_status.get("kill_switch_reason") or ""),
        cooldown_tickers={
            str(ticker).upper()
            for ticker in cooldowns.keys()
            if str(ticker).strip()
        },
        ticker_exposure_notional={
            str(row["ticker"]).upper(): float(row["exposure"] or 0.0)
            for row in ticker_rows
        },
        sleeve_exposure_notional={
            str(row["sleeve"]): float(row["exposure"] or 0.0)
            for row in sleeve_rows
        },
    )


def _estimate_tradingview_notional(
    alert: NormalizedTradingViewAlert,
    spec: TradingViewStrategySpec,
) -> float:
    if alert.signal_price and alert.signal_price > 0:
        return float(alert.signal_price) * float(spec.base_qty)
    return float(spec.base_qty)


def _build_tradingview_event_record(
    alert: NormalizedTradingViewAlert,
    lane: str,
    client_ip: str,
    state: str,
    intent_id: str = "",
    rejection_code: str = "",
    rejection_detail: str = "",
    duplicate_count: int = 0,
) -> EventRecord:
    payload = {
        "schema_version": alert.schema_version,
        "alert_id": alert.alert_id,
        "strategy_id": alert.strategy_id,
        "ticker": alert.ticker,
        "action": alert.action,
        "timeframe": alert.timeframe,
        "event_timestamp": alert.event_timestamp,
        "signal_price": alert.signal_price,
        "indicators": dict(alert.indicators),
        "state": state,
        "lane": lane,
        "intent_id": intent_id,
        "rejection_code": rejection_code,
        "rejection_detail": rejection_detail,
        "client_ip": client_ip,
        "correlation_id": alert.correlation_id,
        "duplicate_count": max(0, int(duplicate_count)),
        "raw_payload": dict(alert.raw_payload),
    }
    detail = {
        "state": state,
        "lane": lane,
        "client_ip": client_ip,
        "alert_id": alert.alert_id,
        "strategy_id": alert.strategy_id,
        "ticker": alert.ticker,
        "action": alert.action,
        "rejection_code": rejection_code,
    }
    return EventRecord(
        event_type="signal",
        source="tradingview",
        source_ref=alert.source_ref,
        retrieved_at=_utc_now_iso(),
        event_timestamp=alert.event_timestamp,
        symbol=alert.ticker,
        headline=f"TradingView alert {state}: {alert.action} {alert.ticker}",
        detail=json.dumps(detail, sort_keys=True),
        confidence=1.0,
        provenance_descriptor=_tradingview_event_descriptor(alert),
        payload=payload,
        event_id=_tradingview_event_id(alert),
    )


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    _logger = logging.getLogger(__name__)
    init_db()

    # Preflight checks
    preflight = _run_preflight_checks(_logger)
    _app.state.preflight = preflight

    # Check IG credentials on startup
    if preflight["ig_broker"] == "missing":
        _logger.warning(
            "IG credentials not configured. Set IG_USERNAME, IG_PASSWORD, IG_API_KEY in .env "
            "to enable broker connection from the control plane."
        )

    # Auto-start scheduler and dispatcher if enabled
    if config.ORCHESTRATOR_ENABLED:
        try:
            result = control.start_scheduler()
            _logger.info("Auto-start scheduler: %s", result.get("status"))
        except Exception as exc:
            _logger.error("Failed to auto-start scheduler: %s", exc)

    if config.DISPATCHER_ENABLED:
        try:
            result = control.start_dispatcher()
            _logger.info("Auto-start dispatcher: %s", result.get("status"))
        except Exception as exc:
            _logger.error("Failed to auto-start dispatcher: %s", exc)

    if config.INTRADAY_ENABLED:
        try:
            result = control.start_intraday()
            _logger.info("Auto-start intraday loop: %s", result.get("status"))
        except Exception as exc:
            _logger.error("Failed to auto-start intraday loop: %s", exc)

    # Start supervision watchdog (checks every 60s, restarts crashed threads)
    _supervisor_stop = asyncio.Event()

    async def _supervisor_loop():
        while not _supervisor_stop.is_set():
            try:
                await asyncio.sleep(60)
                restarted = control.check_and_restart()
                if restarted:
                    _logger.warning("Supervisor restarted: %s", restarted)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _logger.debug("Supervisor tick error: %s", exc)

    supervisor_task = asyncio.create_task(_supervisor_loop())

    yield

    # Graceful shutdown
    _supervisor_stop.set()
    supervisor_task.cancel()

    # Graceful shutdown
    _logger.info("Shutting down background services...")
    try:
        control.stop_scheduler()
    except Exception:
        pass
    try:
        control.stop_dispatcher()
    except Exception:
        pass
    try:
        control.stop_intraday()
    except Exception:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="Trading Bot Control Plane",
        version="1.0.0",
        lifespan=app_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"https://([a-z0-9-]+\.)?seekingalpha\.com",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(ledger_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(PROJECT_ROOT / "app" / "web" / "static")),
        name="static",
    )

    def _store_sa_browser_capture(
        capture,
        *,
        retrieved_at: str,
        capture_source: str,
        log_strategy: str,
    ) -> tuple[int, Optional[dict[str, Any]]]:
        capture_source = str(capture_source or SA_BROWSER_CAPTURE_SOURCE).strip() or SA_BROWSER_CAPTURE_SOURCE
        capture_payload = capture.to_payload()
        store = EventStore()
        store.write_event(
            EventRecord(
                event_type=SA_BROWSER_CAPTURE_EVENT_TYPE,
                source=capture_source,
                source_ref=capture.snapshot.source_ref,
                retrieved_at=retrieved_at,
                event_timestamp=capture.snapshot.updated_at or retrieved_at,
                symbol=capture.ticker,
                headline=f"Seeking Alpha browser capture: {capture.ticker}",
                detail=(
                    f"page_type={capture.page_type or 'unknown'}, "
                    f"rating={capture.snapshot.rating or ''}, "
                    f"grades={len(capture.factor_grades)}"
                ),
                confidence=0.99,
                provenance_descriptor={
                    "ticker": capture.ticker,
                    "url": capture.url,
                    "page_type": capture.page_type,
                    "capture_source": capture_source,
                },
                payload=capture_payload,
            )
        )

        stored_feature_count = 0
        if capture.factor_grades:
            features = normalize_factor_grades(capture.ticker, capture.factor_grades)
            if features:
                fs = FeatureStore(db_path=DB_PATH)
                try:
                    if store_factor_grades(capture.ticker, features, fs, as_of=retrieved_at):
                        stored_feature_count = len(features)
                finally:
                    fs.close()

        layer_score_payload: Optional[dict[str, Any]] = None
        if capture.has_quant_signal:
            layer_score = score_sa_quant_snapshot(
                snapshot=capture.snapshot,
                as_of=retrieved_at,
                source="sa-browser-capture",
            )
            layer_score_payload = layer_score.to_dict()
            store.write_event(
                EventRecord(
                    event_type="signal_layer",
                    source="sa-browser-capture",
                    source_ref=layer_score.provenance_ref or capture.snapshot.source_ref,
                    retrieved_at=retrieved_at,
                    event_timestamp=retrieved_at,
                    symbol=capture.ticker,
                    headline="L8 SA Quant score",
                    detail=(
                        f"ticker={capture.ticker}, score={layer_score.score}, "
                        f"rating={layer_score.details.get('rating', '')}"
                    ),
                    confidence=layer_score.confidence,
                    provenance_descriptor={
                        "layer_id": layer_score.layer_id.value,
                        "ticker": capture.ticker,
                        "as_of": retrieved_at,
                        "capture_source": capture_source,
                    },
                    payload=layer_score_payload,
                )
            )

        _safe_log_event(
            category="SIGNAL",
            headline=f"SA capture received: {capture.ticker}",
            detail=(
                f"source={capture_source}, "
                f"page_type={capture.page_type or 'unknown'}, "
                f"has_quant={capture.has_quant_signal}, "
                f"grades={len(capture.factor_grades)}"
            ),
            ticker=capture.ticker,
            strategy=log_strategy,
        )

        return stored_feature_count, layer_score_payload

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return build_api_health_payload()

    @app.get("/api/preflight")
    def api_preflight() -> dict[str, Any]:
        """Return preflight check results and pipeline status."""
        preflight = getattr(app.state, "preflight", {})
        return {
            "services": preflight,
            "pipeline": control.pipeline_status(),
        }

    @app.get("/api/metrics")
    def api_metrics(days: int = 14):
        payload = build_prometheus_metrics_payload(days=days)
        return Response(
            content=payload,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return build_status_payload()

    @app.get("/api/events")
    def api_events(limit: int = 50):
        return {"items": get_bot_events(limit=limit)}

    @app.get("/api/jobs")
    def api_jobs(limit: int = 50):
        return {"items": get_jobs(limit=limit)}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        item = get_job(job_id)
        if not item:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return {"item": item}

    @app.get("/api/order-actions")
    def api_order_actions(limit: int = 50, status: str = ""):
        return {"items": get_order_actions(limit=limit, status=status or None)}

    @app.get("/api/order-intents")
    def api_order_intents(limit: int = 50, status: str = ""):
        return {"items": get_order_intent_items(limit=limit, status=status)}

    @app.get("/api/order-intents/{intent_id}")
    def api_order_intent_detail(intent_id: str):
        detail = get_order_intent_detail(intent_id)
        if not detail:
            return JSONResponse({"error": "intent_not_found"}, status_code=404)
        return {"item": detail}

    @app.get("/api/broker-health")
    def api_broker_health():
        return build_broker_health_payload()

    # ─── Shared broker endpoints (work without engine running) ──────────

    @app.post("/api/broker/connect")
    def api_broker_connect():
        global _broker
        # Force reconnect
        _broker = None
        broker, err = _get_or_create_broker()
        if not broker:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        info = broker.get_account_info()
        return {
            "ok": True,
            "account": config.IG_ACC_NUMBER,
            "mode": "DEMO" if broker.is_demo else "LIVE",
            "balance": info.balance,
            "equity": info.equity,
            "unrealised_pnl": info.unrealised_pnl,
            "currency": info.currency,
        }

    @app.get("/api/broker/status")
    def api_broker_status():
        if not _broker or not _broker.is_connected():
            return {"connected": False, "message": "Not connected. POST /api/broker/connect first."}
        info = _broker.get_account_info()
        positions = _broker.get_positions()
        return {
            "connected": True,
            "account": config.IG_ACC_NUMBER,
            "mode": "DEMO" if _broker.is_demo else "LIVE",
            "balance": info.balance,
            "equity": info.equity,
            "unrealised_pnl": info.unrealised_pnl,
            "currency": info.currency,
            "open_positions": len(positions),
        }

    @app.get("/api/broker/positions")
    def api_broker_positions():
        if not _broker or not _broker.is_connected():
            return JSONResponse({"error": "Not connected"}, status_code=400)
        positions = _broker.get_positions()
        return {
            "count": len(positions),
            "positions": [
                {
                    "deal_id": p.deal_id,
                    "ticker": p.ticker,
                    "direction": p.direction,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "unrealised_pnl": p.unrealised_pnl,
                    "strategy": p.strategy,
                }
                for p in positions
            ],
        }

    @app.get("/api/broker/market/{epic:path}")
    def api_broker_market(epic: str):
        if not _broker or not _broker.is_connected():
            return JSONResponse({"error": "Not connected"}, status_code=400)
        info = _broker.get_market_info(epic)
        if not info:
            return JSONResponse({"error": f"Market {epic} not found or blocked"}, status_code=404)
        snap = info.get("snapshot", {})
        inst = info.get("instrument", {})
        rules = info.get("dealingRules", {})
        return {
            "epic": epic,
            "name": inst.get("name"),
            "status": snap.get("marketStatus"),
            "bid": snap.get("bid"),
            "offer": snap.get("offer"),
            "high": snap.get("high"),
            "low": snap.get("low"),
            "min_deal_size": rules.get("minDealSize", {}).get("value"),
            "min_stop_distance": rules.get("minNormalStopOrLimitDistance", {}).get("value"),
            "expiry": inst.get("expiry"),
        }

    @app.get("/api/broker/markets")
    def api_broker_markets():
        connected = _broker is not None and _broker.is_connected()
        markets = []
        for ticker, info in config.MARKET_MAP.items():
            entry = {
                "ticker": ticker,
                "epic": info["epic"],
                "ig_name": info.get("ig_name", ""),
                "strategy": info.get("strategy", ""),
                "verified": info.get("verified", False),
            }
            if connected:
                mkt = _broker.get_market_info(info["epic"])
                if mkt:
                    snap = mkt.get("snapshot", {})
                    entry["status"] = snap.get("marketStatus")
                    entry["bid"] = snap.get("bid")
                    entry["offer"] = snap.get("offer")
                    entry["live"] = True
                else:
                    entry["live"] = False
            markets.append(entry)
        return {"connected": connected, "markets": markets}

    @app.post("/api/broker/open-position")
    async def api_broker_open_position(request: Request):
        if not _broker or not _broker.is_connected():
            return JSONResponse({"error": "Not connected"}, status_code=400)

        body = await request.json()
        epic = body.get("epic", "")
        direction = body.get("direction", "BUY").upper()
        size = float(body.get("size", 0))

        if not epic or size <= 0:
            return JSONResponse({"error": "epic, direction, size required"}, status_code=400)
        if direction not in ("BUY", "SELL"):
            return JSONResponse({"error": "direction must be BUY or SELL"}, status_code=400)

        # Resolve ticker from epic (reverse lookup), or use epic as ticker
        ticker = epic
        for t, info in config.MARKET_MAP.items():
            if info["epic"] == epic:
                ticker = t
                break

        if ticker != epic:
            # Use place_long/place_short which handle stop distances etc.
            if direction == "BUY":
                result = _broker.place_long(ticker, size, "api_manual")
            else:
                result = _broker.place_short(ticker, size, "api_manual")
        else:
            # Direct epic — use _place_option_leg for raw epic placement
            result = _broker._place_option_leg(epic, direction, size, epic, "api_manual")

        return {
            "ok": result.success,
            "deal_id": result.order_id,
            "fill_price": result.fill_price,
            "fill_qty": result.fill_qty,
            "message": result.message,
        }

    @app.post("/api/broker/close-position")
    async def api_broker_close_position(request: Request):
        if not _broker or not _broker.is_connected():
            return JSONResponse({"error": "Not connected"}, status_code=400)

        body = await request.json()
        deal_id = body.get("deal_id", "")

        if not deal_id:
            return JSONResponse({"error": "deal_id required"}, status_code=400)

        # Find the position to get direction and size
        positions = _broker.get_positions()
        target = None
        for p in positions:
            if p.deal_id == deal_id:
                target = p
                break

        if not target:
            return JSONResponse({"error": f"No open position with deal_id={deal_id}"}, status_code=404)

        close_direction = "SELL" if target.direction == "long" else "BUY"
        close_payload = {
            "dealId": deal_id,
            "direction": close_direction,
            "size": str(target.size),
            "orderType": "MARKET",
        }

        r = _broker.session.post(
            f"{_broker.base_url}/positions/otc",
            json=close_payload,
            headers={**_broker._headers("1"), "_method": "DELETE"},
        )

        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}, status_code=502)

        import time
        close_ref = r.json().get("dealReference", "")
        if not close_ref:
            return JSONResponse({"ok": False, "error": "No deal reference returned"}, status_code=502)

        time.sleep(1)
        result = _broker._confirm_deal(close_ref, target.ticker, target.strategy, target.size)
        return {
            "ok": result.success,
            "deal_id": result.order_id,
            "fill_price": result.fill_price,
            "message": result.message,
        }

    @app.get("/api/incidents")
    def api_incidents(limit: int = 50):
        return {"items": _visible_incidents(limit=limit)}

    @app.get("/api/control-actions")
    def api_control_actions(limit: int = 50):
        return {"items": get_control_actions(limit=limit)}

    @app.get("/api/reconcile-report")
    def api_reconcile_report():
        return control.reconcile_report()

    @app.get("/api/options/contracts")
    def api_option_contracts(limit: int = 200, index_name: str = "", expiry_type: str = ""):
        return {
            "items": get_option_contracts(
                limit=limit,
                index_name=index_name or None,
                expiry_type=expiry_type or None,
            )
        }

    @app.get("/api/options/summary")
    def api_option_summary():
        return {"items": get_option_contract_summary()}

    @app.get("/api/calibration/runs")
    def api_calibration_runs(limit: int = 20):
        return {"items": get_calibration_runs(limit=limit)}

    @app.get("/api/calibration/points")
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

    @app.get("/api/strategy/parameter-sets")
    def api_strategy_parameter_sets(limit: int = 50, strategy_key: str = "", status: str = ""):
        return {
            "items": get_strategy_parameter_sets(
                limit=limit,
                strategy_key=strategy_key or None,
                status=status or None,
            )
        }

    @app.get("/api/strategy/promotions")
    def api_strategy_promotions(limit: int = 50, strategy_key: str = ""):
        return {
            "items": get_strategy_promotions(
                limit=limit,
                strategy_key=strategy_key or None,
            )
        }

    @app.get("/api/strategy/active")
    def api_strategy_active(strategy_key: str = "ibs_credit_spreads"):
        return {
            "shadow": get_active_strategy_parameter_set(strategy_key, status="shadow"),
            "staged_live": get_active_strategy_parameter_set(strategy_key, status="staged_live"),
            "live": get_active_strategy_parameter_set(strategy_key, status="live"),
        }

    @app.get("/api/strategy/promotion-gate")
    def api_strategy_promotion_gate(
        strategy_key: str = "ibs_credit_spreads",
        cooldown_hours: int = 24,
    ):
        return build_promotion_gate_report(
            strategy_key=strategy_key,
            cooldown_hours=cooldown_hours,
        )

    @app.get("/api/log-tail")
    def api_log_tail(lines: int = 200):
        try:
            text = _tail_file(control.process_log, lines=lines)
        except FileNotFoundError:
            text = ""
        return JSONResponse({"log": text})

    @app.get("/api/risk/briefing")
    def api_risk_briefing():
        return build_risk_briefing_payload()

    @app.get("/api/signal-shadow")
    def api_signal_shadow():
        return enrich_signal_shadow_payload(get_signal_shadow_report())

    @app.get("/api/execution-quality")
    def api_execution_quality(days: int = 30):
        return get_execution_quality_payload(days=days)

    @app.get("/api/analytics/portfolio")
    def api_portfolio_analytics(days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS):
        return build_portfolio_analytics_payload(days=days)

    @app.get("/api/charts/equity-curve")
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

    @app.get("/api/tradingview/alerts")
    def api_tradingview_alerts(limit: int = 50, state: str = "", strategy_id: str = ""):
        store = EventStore(db_path=DB_PATH)
        requested_state = str(state or "").strip().lower()
        requested_strategy = str(strategy_id or "").strip().lower()
        rows = store.list_events(limit=max(limit * 3, limit), source="tradingview")

        items: list[dict[str, Any]] = []
        for row in rows:
            payload = _decode_json_payload(row.get("payload"))
            payload_state = str(payload.get("state") or "").strip().lower()
            payload_strategy = str(payload.get("strategy_id") or "").strip().lower()
            if requested_state and payload_state != requested_state:
                continue
            if requested_strategy and payload_strategy != requested_strategy:
                continue
            row["payload"] = payload
            items.append(row)
            if len(items) >= limit:
                break
        return {"items": items}

    @app.post("/api/webhooks/tradingview")
    async def tradingview_webhook(request: Request, token: str = ""):
        payload: Optional[dict[str, Any]] = None
        alert: Optional[NormalizedTradingViewAlert] = None
        client_ip = request.client.host if request.client else "-"
        try:
            payload = parse_json_payload(
                raw_body=await request.body(),
                max_payload_bytes=config.TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES,
            )
            provided_token = extract_auth_token(
                payload=payload,
                header_token=request.headers.get("x-webhook-token", ""),
                query_token=token,
            )
            validate_expected_token(
                expected_token=config.TRADINGVIEW_WEBHOOK_TOKEN,
                provided_token=provided_token,
            )
            alert = normalize_tradingview_alert(
                payload=payload,
                registry=get_tradingview_strategy_registry(),
                max_age_seconds=config.TRADINGVIEW_MAX_SIGNAL_AGE_SECONDS,
            )
        except WebhookValidationError as exc:
            _safe_log_event(
                category="REJECTION",
                headline="TradingView webhook rejected",
                detail=build_audit_detail(
                    reason=exc.message,
                    client_ip=client_ip,
                    payload=payload,
                ),
                ticker=(str(payload.get("symbol") or payload.get("ticker")) if payload else None),
                strategy="tradingview_webhook",
            )
            return JSONResponse(
                {"ok": False, "error": exc.code, "detail": exc.message},
                status_code=exc.status_code,
            )

        spec = get_tradingview_strategy_registry()[alert.strategy_id]
        store = EventStore(db_path=DB_PATH)
        existing = store.get_event(_tradingview_event_id(alert))
        if existing:
            existing_payload = _decode_json_payload(existing.get("payload"))
            duplicate_count = int(existing_payload.get("duplicate_count") or 0) + 1
            store.write_event(
                _build_tradingview_event_record(
                    alert=alert,
                    lane=str(existing_payload.get("lane") or "unknown"),
                    client_ip=client_ip,
                    state=str(existing_payload.get("state") or "accepted"),
                    intent_id=str(existing_payload.get("intent_id") or ""),
                    rejection_code=str(existing_payload.get("rejection_code") or ""),
                    rejection_detail=str(existing_payload.get("rejection_detail") or ""),
                    duplicate_count=duplicate_count,
                )
            )
            _safe_log_event(
                category="SIGNAL",
                headline=f"TradingView alert duplicate: {alert.action} {alert.ticker}",
                detail=build_audit_detail(reason="duplicate", client_ip=client_ip, payload=payload),
                ticker=alert.ticker,
                strategy=alert.strategy_id,
            )
            return {
                "ok": True,
                "state": "duplicate",
                "ticker": alert.ticker,
                "action": alert.action,
                "strategy": alert.strategy_id,
                "timeframe": alert.timeframe,
                "duplicate_count": duplicate_count,
            }

        lane, _lane_item = _resolve_tradingview_lane(alert.strategy_id, db_path=DB_PATH)
        if lane == "missing":
            store.write_event(
                _build_tradingview_event_record(
                    alert=alert,
                    lane=lane,
                    client_ip=client_ip,
                    state="rejected",
                    rejection_code="NO_LANE_DATA",
                    rejection_detail="Strategy has no shadow/staged_live/live parameter set.",
                )
            )
            _safe_log_event(
                category="REJECTION",
                headline="TradingView webhook rejected",
                detail=build_audit_detail(reason="no lane data", client_ip=client_ip, payload=payload),
                ticker=alert.ticker,
                strategy=alert.strategy_id,
            )
            return JSONResponse(
                {"ok": False, "error": "NO_LANE_DATA", "detail": "Strategy has no configured promotion lane."},
                status_code=409,
            )

        if lane != "live":
            store.write_event(
                _build_tradingview_event_record(
                    alert=alert,
                    lane=lane,
                    client_ip=client_ip,
                    state="audit_only",
                )
            )
            _safe_log_event(
                category="SIGNAL",
                headline=f"TradingView alert audited ({lane}): {alert.action} {alert.ticker}",
                detail=build_audit_detail(reason=f"audit_only:{lane}", client_ip=client_ip, payload=payload),
                ticker=alert.ticker,
                strategy=alert.strategy_id,
            )
            return {
                "ok": True,
                "state": "audit_only",
                "lane": lane,
                "message": "TradingView alert accepted and stored for audit. No order intent created in this lane.",
                "ticker": alert.ticker,
                "action": alert.action,
                "strategy": alert.strategy_id,
                "timeframe": alert.timeframe,
            }

        engine_status = control.status()
        route_state = _build_tradingview_route_state(engine_status)
        requirements = StrategyRequirements(**dict(spec.requirements))
        route_decision = _build_tradingview_router(spec).resolve(
            RouteIntent(
                strategy_id=spec.strategy_id,
                sleeve=spec.sleeve,
                ticker=alert.ticker,
                requirements=requirements,
            ),
            policy_state=route_state,
        )
        if not route_decision.allowed:
            store.write_event(
                _build_tradingview_event_record(
                    alert=alert,
                    lane=lane,
                    client_ip=client_ip,
                    state="rejected",
                    rejection_code=str(route_decision.reason_code).upper(),
                    rejection_detail=str(route_decision.message),
                )
            )
            _safe_log_event(
                category="REJECTION",
                headline="TradingView webhook rejected",
                detail=build_audit_detail(reason=route_decision.message, client_ip=client_ip, payload=payload),
                ticker=alert.ticker,
                strategy=alert.strategy_id,
            )
            return JSONResponse(
                {"ok": False, "error": str(route_decision.reason_code).upper(), "detail": route_decision.message},
                status_code=403 if route_decision.reason_code in {"kill_switch_active", "market_cooldown_active"} else 422,
            )

        side_str, is_exit = _tradingview_action_semantics(alert.action)
        risk_context = _build_tradingview_risk_context(engine_status, db_path=DB_PATH)
        if risk_context is not None and not is_exit:
            notional = _estimate_tradingview_notional(alert, spec)
            risk_decision = evaluate_pre_trade_risk(
                request=RiskOrderRequest(
                    ticker=alert.ticker,
                    sleeve=spec.sleeve,
                    order_exposure_notional=notional,
                ),
                context=risk_context,
                limits=_TRADINGVIEW_RISK_LIMITS,
            )
            if not risk_decision.approved:
                store.write_event(
                    _build_tradingview_event_record(
                        alert=alert,
                        lane=lane,
                        client_ip=client_ip,
                        state="rejected",
                        rejection_code=risk_decision.rule_id,
                        rejection_detail=risk_decision.message,
                    )
                )
                _safe_log_event(
                    category="REJECTION",
                    headline="TradingView webhook rejected",
                    detail=build_audit_detail(reason=risk_decision.message, client_ip=client_ip, payload=payload),
                    ticker=alert.ticker,
                    strategy=alert.strategy_id,
                )
                return JSONResponse(
                    {"ok": False, "error": risk_decision.rule_id, "detail": risk_decision.message},
                    status_code=409,
                )

        promo_decision = evaluate_promotion_gate(
            strategy_key=spec.strategy_id,
            is_exit=is_exit,
            config=PromotionGateConfig(enabled=True, require_live_set=True),
            db_path=DB_PATH,
        )
        if not promo_decision.allowed:
            store.write_event(
                _build_tradingview_event_record(
                    alert=alert,
                    lane=lane,
                    client_ip=client_ip,
                    state="rejected",
                    rejection_code=promo_decision.reason_code,
                    rejection_detail=promo_decision.message,
                )
            )
            _safe_log_event(
                category="REJECTION",
                headline="TradingView webhook rejected",
                detail=build_audit_detail(reason=promo_decision.message, client_ip=client_ip, payload=payload),
                ticker=alert.ticker,
                strategy=alert.strategy_id,
            )
            return JSONResponse(
                {"ok": False, "error": promo_decision.reason_code, "detail": promo_decision.message},
                status_code=409,
            )

        resolved = route_decision.resolution
        metadata = {
            "source": "tradingview_webhook",
            "source_ref": alert.source_ref,
            "schema_version": alert.schema_version,
            "alert_id": alert.alert_id,
            "signal_timestamp": alert.event_timestamp,
            "signal_price": alert.signal_price,
            "indicators": dict(alert.indicators),
            "raw_payload": payload,
            "is_exit": is_exit,
        }
        intent = OrderIntent(
            strategy_id=spec.strategy_id,
            strategy_version=spec.strategy_version,
            sleeve=spec.sleeve,
            account_type=resolved.account_type if resolved is not None else spec.account_type,
            broker_target=resolved.broker_name if resolved is not None else spec.broker_target,
            instrument=alert.ticker,
            side=side_str,
            qty=spec.base_qty,
            order_type="MARKET",
            risk_tags=list(spec.risk_tags),
            metadata=metadata,
        )
        envelope = create_order_intent_envelope(
            intent=intent,
            action_type="tradingview_signal",
            actor="system",
            correlation_id=alert.correlation_id,
            db_path=DB_PATH,
        )
        intent_id = envelope.get("intent_id", "")
        store.write_event(
            _build_tradingview_event_record(
                alert=alert,
                lane=lane,
                client_ip=client_ip,
                state="intent_created",
                intent_id=intent_id,
            )
        )

        _safe_log_event(
            category="SIGNAL",
            headline=f"TradingView alert accepted: {alert.action} {alert.ticker}",
            detail=build_audit_detail(reason="intent_created", client_ip=client_ip, payload=payload),
            ticker=alert.ticker,
            strategy=alert.strategy_id,
        )

        return {
            "ok": True,
            "state": "intent_created",
            "message": "TradingView alert accepted and order intent created.",
            "ticker": alert.ticker,
            "action": alert.action,
            "strategy": alert.strategy_id,
            "timeframe": alert.timeframe,
            "intent_id": intent_id,
            "lane": lane,
        }

    @app.post("/api/actions/start", response_class=HTMLResponse)
    def start_bot(mode: str = Form(default=config.TRADING_MODE)):
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="start_bot", status="running", mode=mode)
        try:
            result = control.start(mode=mode)
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            return action_message(f"Start failed: {exc}", ok=False)
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/stop", response_class=HTMLResponse)
    def stop_bot():
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="stop_bot", status="running")
        try:
            result = control.stop()
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            return action_message(f"Stop failed: {exc}", ok=False)
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/pause", response_class=HTMLResponse)
    def pause_bot():
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="pause_bot", status="running")
        try:
            result = control.pause()
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            return action_message(f"Pause failed: {exc}", ok=False)
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/resume", response_class=HTMLResponse)
    def resume_bot():
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="resume_bot", status="running")
        try:
            result = control.resume()
        except Exception as exc:
            update_job(job_id, status="failed", error=str(exc))
            return action_message(f"Resume failed: {exc}", ok=False)
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/scan-now", response_class=HTMLResponse)
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

    @app.post("/api/actions/reconcile", response_class=HTMLResponse)
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

    @app.post("/api/actions/signal-shadow-run", response_class=HTMLResponse)
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

    @app.post("/api/actions/signal-tier1-run", response_class=HTMLResponse)
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

    @app.post("/api/actions/close-spread", response_class=HTMLResponse)
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

    @app.post("/api/actions/kill-switch-enable", response_class=HTMLResponse)
    def kill_switch_enable(reason: str = Form(default="Manual operator kill switch")):
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="kill_switch_enable", status="running", detail=reason)
        result = control.set_kill_switch(active=True, reason=reason, actor="operator")
        if result["ok"]:
            update_job(job_id, status="completed", detail=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/kill-switch-disable", response_class=HTMLResponse)
    def kill_switch_disable(reason: str = Form(default="Manual clear from control plane")):
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="kill_switch_disable", status="running", detail=reason)
        result = control.set_kill_switch(active=False, reason=reason, actor="operator")
        if result["ok"]:
            update_job(job_id, status="completed", detail=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/risk-throttle", response_class=HTMLResponse)
    def risk_throttle(
        throttle_pct: float = Form(default=100.0),
        reason: str = Form(default="Manual risk throttle"),
    ):
        clamped = min(100.0, max(10.0, float(throttle_pct)))
        pct = clamped / 100.0
        job_id = str(uuid.uuid4())
        detail = f"{clamped:.0f}% ({reason})"
        create_job(job_id=job_id, job_type="risk_throttle", status="running", detail=detail)
        result = control.set_risk_throttle(pct=pct, reason=reason, actor="operator")
        if result["ok"]:
            update_job(job_id, status="completed", detail=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/cooldown-set", response_class=HTMLResponse)
    def cooldown_set(
        ticker: str = Form(default=""),
        minutes: int = Form(default=30),
        reason: str = Form(default="Manual market cooldown"),
    ):
        clean_ticker = ticker.strip().upper()
        if not clean_ticker:
            return action_message("Ticker is required for cooldown.", ok=False)
        duration = max(1, int(minutes))
        job_id = str(uuid.uuid4())
        detail = f"{clean_ticker} {duration}m ({reason})"
        create_job(job_id=job_id, job_type="cooldown_set", status="running", detail=detail)
        result = control.set_market_cooldown(
            ticker=clean_ticker, minutes=duration, reason=reason, actor="operator"
        )
        if result["ok"]:
            update_job(job_id, status="completed", detail=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/cooldown-clear", response_class=HTMLResponse)
    def cooldown_clear(
        ticker: str = Form(default=""),
        reason: str = Form(default="Manual cooldown clear"),
    ):
        clean_ticker = ticker.strip().upper()
        if not clean_ticker:
            return action_message("Ticker is required to clear cooldown.", ok=False)
        job_id = str(uuid.uuid4())
        detail = f"{clean_ticker} ({reason})"
        create_job(job_id=job_id, job_type="cooldown_clear", status="running", detail=detail)
        result = control.clear_market_cooldown(
            ticker=clean_ticker, reason=reason, actor="operator"
        )
        if result["ok"]:
            update_job(job_id, status="completed", detail=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    # ─── Pipeline control endpoints ──────────────────────────────────────

    @app.post("/api/actions/scheduler-start", response_class=HTMLResponse)
    def scheduler_start_action():
        result = control.start_scheduler()
        ok = result.get("status") != "error"
        return action_message(f"Scheduler: {result['status']}", ok=ok)

    @app.post("/api/actions/scheduler-stop", response_class=HTMLResponse)
    def scheduler_stop_action():
        result = control.stop_scheduler()
        return action_message(f"Scheduler: {result['status']}", ok=True)

    @app.post("/api/actions/dispatcher-start", response_class=HTMLResponse)
    def dispatcher_start_action():
        result = control.start_dispatcher()
        ok = result.get("status") != "error"
        return action_message(f"Dispatcher: {result['status']}", ok=ok)

    @app.post("/api/actions/dispatcher-stop", response_class=HTMLResponse)
    def dispatcher_stop_action():
        result = control.stop_dispatcher()
        return action_message(f"Dispatcher: {result['status']}", ok=True)

    @app.post("/api/actions/run-daily-dag", response_class=HTMLResponse)
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

    @app.get("/api/pipeline-status")
    def pipeline_status_api():
        return control.pipeline_status()

    @app.post("/api/actions/discover-options", response_class=HTMLResponse)
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

    @app.post("/api/actions/calibrate-options", response_class=HTMLResponse)
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

    @app.post("/api/actions/strategy-params/create", response_class=HTMLResponse)
    def strategy_params_create_action(
        strategy_key: str = Form(default="ibs_credit_spreads"),
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

        if clean_strategy != "ibs_credit_spreads":
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

    @app.post("/api/actions/strategy-params/promote", response_class=HTMLResponse)
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
                strategy_key=str(set_item.get("strategy_key") or "ibs_credit_spreads"),
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

    @app.get("/", response_class=HTMLResponse)
    def overview_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "overview.html",
            _page_context(request=request, page_key="overview", title="Overview | Trading Bot"),
        )

    @app.get("/overview", response_class=HTMLResponse)
    def overview_page_alias(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "overview.html",
            _page_context(request=request, page_key="overview", title="Overview | Trading Bot"),
        )

    @app.get("/trading", response_class=HTMLResponse)
    def trading_page(request: Request):
        ctx = _page_context(request=request, page_key="trading", title="Trading | Trading Bot")
        ctx["market_map"] = config.MARKET_MAP
        # Default chart EPIC — SPY (US 500)
        spy_info = config.MARKET_MAP.get("SPY", {})
        ctx["default_epic"] = spy_info.get("epic", "IX.D.SPTRD.DAILY.IP")
        return TEMPLATES.TemplateResponse(request, "trading.html", ctx)

    @app.get("/research", response_class=HTMLResponse)
    def research_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "research_page.html",
            _page_context(request=request, page_key="research", title="Research | Trading Bot"),
        )

    @app.get("/incidents", response_class=HTMLResponse)
    def incidents_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "incidents_page.html",
            _page_context(request=request, page_key="incidents", title="Incidents & Jobs | Trading Bot"),
        )

    @app.get("/intel", response_class=HTMLResponse)
    def intel_council_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "intel_council_page.html",
            _page_context(request=request, page_key="intel", title="Intel Council | Trading Bot"),
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "settings_page.html",
            _page_context(request=request, page_key="settings", title="Settings | Trading Bot"),
        )

    @app.get("/api/settings")
    def api_get_settings():
        return _get_editable_settings()

    @app.post("/api/settings", response_class=HTMLResponse)
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

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy_single_page(request: Request):
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

    @app.get("/fragments/top-strip", response_class=HTMLResponse)
    def top_strip_fragment(request: Request):
        payload = build_status_payload()
        latest = _visible_incidents(limit=1)
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

    @app.get("/fragments/status", response_class=HTMLResponse)
    def status_fragment(request: Request):
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

    @app.get("/fragments/jobs", response_class=HTMLResponse)
    def jobs_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_jobs.html",
            {"request": request, "jobs": get_jobs(limit=20)},
        )

    @app.get("/fragments/job-detail", response_class=HTMLResponse)
    def job_detail_fragment(request: Request, job_id: str = ""):
        selected_id = job_id.strip()
        if not selected_id:
            for row in get_jobs(limit=40):
                if row.get("job_type") in {"discover_options", "calibrate_options"}:
                    selected_id = row.get("id", "")
                    break
        item = get_job(selected_id) if selected_id else None
        parsed_result = _parse_job_result(item.get("result", "")) if item else None
        return TEMPLATES.TemplateResponse(
            request,
            "_job_detail.html",
            {
                "request": request,
                "job": item,
                "parsed_result": parsed_result,
            },
        )

    @app.get("/fragments/events", response_class=HTMLResponse)
    def events_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_events.html",
            {"request": request, "events": get_bot_events(limit=25)},
        )

    @app.get("/fragments/order-actions", response_class=HTMLResponse)
    def order_actions_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_order_actions.html",
            {"request": request, "order_actions": get_order_actions(limit=25)},
        )

    @app.get("/fragments/intent-audit", response_class=HTMLResponse)
    def intent_audit_fragment(request: Request, intent_id: str = ""):
        intents = get_order_intent_items(limit=20, status="")
        selected_id = intent_id.strip() or (intents[0]["intent_id"] if intents else "")
        selected_detail = get_order_intent_detail(selected_id) if selected_id else None
        return TEMPLATES.TemplateResponse(
            request,
            "_intent_audit.html",
            {
                "request": request,
                "intents": intents,
                "selected_intent_id": selected_id,
                "selected_detail": selected_detail,
            },
        )

    @app.get("/fragments/broker-health", response_class=HTMLResponse)
    def broker_health_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_broker_health.html",
            {
                "request": request,
                "broker_health": build_broker_health_payload(),
            },
        )

    @app.get("/fragments/broker-panel", response_class=HTMLResponse)
    def broker_panel_fragment(request: Request):
        connected = _broker is not None and _broker.is_connected()
        ctx: dict[str, Any] = {"request": request, "connected": connected}
        if connected:
            info = _broker.get_account_info()
            positions = _broker.get_positions()
            ctx["account"] = config.IG_ACC_NUMBER
            ctx["mode"] = "DEMO" if _broker.is_demo else "LIVE"
            ctx["balance"] = info.balance
            ctx["equity"] = info.equity
            ctx["unrealised_pnl"] = info.unrealised_pnl
            ctx["currency"] = info.currency
            ctx["open_positions"] = len(positions)
        return TEMPLATES.TemplateResponse(request, "_broker_panel.html", ctx)

    @app.get("/fragments/market-browser", response_class=HTMLResponse)
    def market_browser_fragment(request: Request):
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
            if connected:
                mkt = _broker.get_market_info(info["epic"])
                if mkt:
                    snap = mkt.get("snapshot", {})
                    entry["status"] = snap.get("marketStatus")
                    entry["bid"] = snap.get("bid")
                    entry["offer"] = snap.get("offer")
            markets.append(entry)
        return TEMPLATES.TemplateResponse(
            request,
            "_market_browser.html",
            {"request": request, "connected": connected, "markets": markets},
        )

    @app.get("/fragments/open-positions", response_class=HTMLResponse)
    def open_positions_fragment(request: Request):
        connected = _broker is not None and _broker.is_connected()
        positions = []
        if connected:
            for p in _broker.get_positions():
                positions.append({
                    "deal_id": p.deal_id,
                    "ticker": p.ticker,
                    "direction": p.direction,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "unrealised_pnl": p.unrealised_pnl,
                })
        return TEMPLATES.TemplateResponse(
            request,
            "_open_positions.html",
            {"request": request, "connected": connected, "positions": positions},
        )

    @app.post("/api/actions/manual-trade", response_class=HTMLResponse)
    def manual_trade_action(
        epic: str = Form(default=""),
        direction: str = Form(default="BUY"),
        size: float = Form(default=0.5),
    ):
        if not epic:
            return action_message("EPIC is required.", ok=False)
        if direction not in ("BUY", "SELL"):
            return action_message("Direction must be BUY or SELL.", ok=False)
        if size <= 0:
            return action_message("Size must be positive.", ok=False)

        broker, err = _get_or_create_broker()
        if not broker:
            return action_message(f"Broker not available: {err}", ok=False)

        # Reverse-lookup ticker from EPIC
        ticker = epic
        for t, info in config.MARKET_MAP.items():
            if info["epic"] == epic:
                ticker = t
                break

        if direction == "BUY":
            result = broker.place_long(ticker, size, "manual_trade")
        else:
            result = broker.place_short(ticker, size, "manual_trade")

        if result.success:
            return action_message(
                f"{direction} {ticker} @ {result.fill_price} — deal {result.order_id or 'confirmed'}",
                ok=True,
            )
        return action_message(f"Trade failed: {result.message}", ok=False)

    @app.post("/api/actions/close-deal", response_class=HTMLResponse)
    def close_deal_action(deal_id: str = Form(default="")):
        if not deal_id:
            return action_message("deal_id is required.", ok=False)

        broker, err = _get_or_create_broker()
        if not broker:
            return action_message(f"Broker not available: {err}", ok=False)

        positions = broker.get_positions()
        target = None
        for p in positions:
            if p.deal_id == deal_id:
                target = p
                break
        if not target:
            return action_message(f"No open position with deal_id={deal_id}", ok=False)

        close_direction = "SELL" if target.direction == "long" else "BUY"
        result = broker._close_option_leg(deal_id, close_direction, target.size)
        if result.success:
            return action_message(
                f"Closed {target.ticker} {target.direction} @ {result.fill_price}",
                ok=True,
            )
        return action_message(f"Close failed: {result.message}", ok=False)

    @app.get("/api/charts/market-prices")
    def api_market_prices(epic: str = "", resolution: str = "HOUR", points: int = 48):
        """Fetch price history from IG for lightweight-charts."""
        _VALID_RESOLUTIONS = {"MINUTE", "MINUTE_5", "MINUTE_15", "MINUTE_30", "HOUR", "HOUR_4", "DAY", "WEEK"}
        if not epic or resolution not in _VALID_RESOLUTIONS:
            return []
        points = max(1, min(points, 200))

        broker, err = _get_or_create_broker()
        if not broker:
            return []

        try:
            r = broker.session.get(
                f"{broker.base_url}/prices/{epic}",
                params={"resolution": resolution, "max": points, "pageSize": points},
                headers=broker._headers("3"),
                timeout=broker._TIMEOUT,
            )
            if r.status_code != 200:
                return []

            data = r.json()
            result = []
            for candle in data.get("prices", []):
                snap_time = candle.get("snapshotTime", "")
                close_price = candle.get("closePrice", {})
                mid = close_price.get("bid")
                if mid is None:
                    continue
                # IG returns snapshotTime as "2026/03/04 14:00:00"
                # lightweight-charts needs UTC epoch seconds
                try:
                    dt = datetime.strptime(snap_time, "%Y/%m/%d %H:%M:%S")
                    epoch = int(dt.replace(tzinfo=timezone.utc).timestamp())
                except (ValueError, TypeError):
                    continue
                result.append({"time": epoch, "value": float(mid)})
            return result
        except Exception:
            return []

    @app.get("/fragments/ledger", response_class=HTMLResponse)
    def ledger_fragment(request: Request):
        snapshot = get_unified_ledger_snapshot(nav_limit=25)
        reconcile = get_ledger_reconcile_report(stale_after_minutes=30)
        return TEMPLATES.TemplateResponse(
            request,
            "_ledger_snapshot.html",
            {
                "request": request,
                "ledger": snapshot,
                "reconcile": reconcile,
            },
        )

    @app.get("/fragments/risk-briefing", response_class=HTMLResponse)
    def risk_briefing_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_risk_briefing.html",
            {
                "request": request,
                "risk_briefing": build_risk_briefing_payload(),
            },
        )

    @app.get("/fragments/incidents", response_class=HTMLResponse)
    def incidents_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_incidents.html",
            {"request": request, "incidents": _visible_incidents(limit=25)},
        )

    @app.get("/fragments/control-actions", response_class=HTMLResponse)
    def control_actions_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_control_actions.html",
            {"request": request, "control_actions": get_control_actions(limit=25)},
        )

    @app.get("/fragments/intelligence-feed", response_class=HTMLResponse)
    def intelligence_feed_fragment(request: Request):
        from datetime import datetime, timezone as tz

        # Macro regime
        macro_regime = ""
        try:
            from intelligence.feature_store import FeatureStore
            from intelligence.macro_regime import MacroRegimeClassifier
            fs = FeatureStore()
            try:
                result = MacroRegimeClassifier(feature_store=fs).classify()
                macro_regime = result.regime.value if result else ""
            finally:
                fs.close()
        except Exception:
            pass

        # Signal layer freshness
        layers = []
        try:
            from app.signal.types import LayerId
            from intelligence.event_store import EventStore
            es = EventStore()
            try:
                for lid in LayerId:
                    latest = es.get_latest_by_layer(lid.value)
                    fresh = latest is not None
                    layers.append({"id": lid.value, "fresh": fresh, "stale": False})
            except Exception:
                pass
            finally:
                es.close()
        except Exception:
            pass

        # Top candidates from latest composite
        candidates = []

        # AI verdicts (if any)
        ai_verdicts = {}

        return TEMPLATES.TemplateResponse(
            request,
            "_intelligence_feed.html",
            {
                "request": request,
                "as_of": datetime.now(tz.utc).isoformat(),
                "macro_regime": macro_regime,
                "layers": layers,
                "candidates": candidates,
                "ai_verdicts": ai_verdicts,
            },
        )

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

    @app.get("/api/sa/snapshots")
    def api_sa_symbol_snapshots(limit: int = 5):
        cards = _build_sa_symbol_capture_cards(limit=min(max(limit, 1), 20))
        return {"ok": True, "count": len(cards), "items": cards}

    @app.get("/fragments/sa-symbol-captures", response_class=HTMLResponse)
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

    @app.get("/fragments/intel-council", response_class=HTMLResponse)
    def intel_council_fragment(request: Request):
        """Render the LLM council analysis feed."""
        from intelligence.event_store import EventStore
        from datetime import datetime, timezone as tz

        # Check for active council jobs
        active_jobs = []
        try:
            from data.trade_db import get_conn, DB_PATH
            conn = get_conn(DB_PATH)
            running = conn.execute(
                """SELECT id, status, detail, created_at FROM jobs
                   WHERE job_type = 'intel_analysis' AND status IN ('queued', 'running')
                   ORDER BY created_at DESC LIMIT 5"""
            ).fetchall()
            conn.close()
            now = datetime.now(tz.utc)
            for r in running:
                created = r[3] or ""
                elapsed = ""
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=tz.utc)
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
                import re as _summary_re
                parts = _summary_re.split(r'\[(\w+)\]\s*', summary_raw)
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

    @app.get("/fragments/intel-costs", response_class=HTMLResponse)
    def intel_costs_fragment(request: Request):
        """Render council cost monitoring panel."""
        from intelligence.intel_pipeline import get_council_cost_summary
        costs = get_council_cost_summary()
        return TEMPLATES.TemplateResponse(
            request,
            "_intel_costs.html",
            {"request": request, "costs": costs},
        )

    @app.get("/fragments/intel-pipeline-summary", response_class=HTMLResponse)
    def intel_pipeline_summary_fragment(request: Request):
        """Render pipeline summary sidebar with real DB stage counts."""
        from intelligence.event_store import EventStore

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

    @app.post("/api/intel/challenge", response_class=HTMLResponse)
    async def intel_challenge(request: Request):
        """User challenges/questions a council analysis — re-runs through LLM council with context."""
        form = await request.form()
        analysis_id = form.get("analysis_id", "")
        challenge_text = form.get("challenge_text", "").strip()
        if not challenge_text:
            return HTMLResponse('<span class="text-[11px] text-red-400">Please enter a challenge or question.</span>')

        # Find the original analysis
        from intelligence.event_store import EventStore
        es = EventStore()
        events = es.list_events(limit=50, event_type="intel_analysis")

        original = None
        for ev in events:
            payload = ev.get("payload", {}) if isinstance(ev, dict) else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            if payload.get("analysis_id") == analysis_id:
                original = payload
                break

        if not original:
            return HTMLResponse('<span class="text-[11px] text-red-400">Original analysis not found.</span>')

        # Build challenge content with original context
        challenge_content = (
            f"ORIGINAL ANALYSIS:\n"
            f"Source: {original.get('source', '')}\n"
            f"Title: {original.get('title', '')}\n"
            f"Summary: {original.get('summary', '')}\n"
            f"Trade ideas: {json.dumps(original.get('trade_ideas', []))}\n"
            f"Risk factors: {json.dumps(original.get('risk_factors', []))}\n\n"
            f"USER CHALLENGE:\n{challenge_text}\n\n"
            f"Please respond to the user's challenge. Re-evaluate the original analysis considering their point. "
            f"If they raise valid concerns, adjust your assessment. Be specific about what changes and what doesn't."
        )

        submission = IntelSubmission(
            source="challenge",
            content=challenge_content,
            url=original.get("url", ""),
            title=f"Challenge: {original.get('title', '')[:60]}",
        )

        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="intel_analysis", status="queued",
                    detail=f"Challenge: {challenge_text[:80]}")
        analyze_intel_async(submission, job_id)

        return HTMLResponse(
            f'<span class="text-[11px] text-emerald-400">Challenge sent to council (job {job_id[:8]}). '
            f'Refresh in ~30s to see the response above.</span>'
        )

    @app.post("/api/intel/submit", response_class=HTMLResponse)
    async def intel_submit(request: Request):
        """Submit content directly from the UI for council analysis."""
        form = await request.form()
        content = form.get("content", "").strip()
        if not content:
            return HTMLResponse('<span class="text-[11px] text-red-400">Please enter some content.</span>')

        # Detect if it's an X link
        urls = _re.findall(r'https?://\S+', content)
        url = urls[0] if urls else ""
        is_x = any(d in content for d in ["twitter.com/", "x.com/", "nitter.", "vxtwitter.com/"])

        # If X link, try to fetch the tweet
        if is_x and url:
            tweet_data = _fetch_tweet_from_url(url)
            if tweet_data:
                content = tweet_data["text"]
                if tweet_data.get("author"):
                    content = f"@{tweet_data['author']}: {content}"
                if tweet_data.get("created_at"):
                    content += f"\n\n[Posted: {tweet_data['created_at']}]"

        submission = IntelSubmission(
            source="x_twitter" if is_x else "manual",
            content=content,
            url=url,
            title="Manual submission" if not is_x else "Forwarded via UI",
        )

        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="intel_analysis", status="queued",
                    detail=f"UI intel: {content[:80]}")
        analyze_intel_async(submission, job_id)

        return HTMLResponse(
            f'<span class="text-[11px] text-emerald-400">Queued for analysis (job {job_id[:8]}). '
            f'Results will appear in the feed.</span>'
        )

    # ── Idea Pipeline endpoints ──────────────────────────────────────────

    @app.get("/api/ideas")
    def list_ideas(stage: str = None, ticker: str = None, limit: int = 50):
        """List trade ideas with optional filters."""
        ideas = get_trade_ideas(stage=stage, ticker=ticker, limit=limit)
        return {"ideas": ideas, "count": len(ideas)}

    @app.get("/api/ideas/{idea_id}")
    def get_idea_detail(idea_id: str):
        """Get a single idea with its transition history."""
        idea = get_trade_idea(idea_id)
        if not idea:
            return JSONResponse({"error": "Idea not found"}, status_code=404)
        transitions = get_idea_transitions(idea_id)
        return {"idea": idea, "transitions": transitions}

    @app.post("/api/ideas/{idea_id}/promote")
    async def promote_idea_endpoint(idea_id: str, request: Request):
        """Promote an idea to the next pipeline stage."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        # Accept both form data (HTMX) and JSON
        ct = request.headers.get("content-type", "")
        if "json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        target = body.get("target_stage", "")
        reason = body.get("reason", "")
        result = mgr.promote_idea(idea_id, target, actor="operator", reason=reason)
        if not result.get("success"):
            return JSONResponse(result, status_code=400)
        return result

    @app.post("/api/ideas/{idea_id}/reject")
    async def reject_idea_endpoint(idea_id: str, request: Request):
        """Reject an idea."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        ct = request.headers.get("content-type", "")
        if "json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        reason = body.get("reason", "")
        result = mgr.reject_idea(idea_id, reason=reason, actor="operator")
        if not result.get("success"):
            return JSONResponse(result, status_code=400)
        return result

    @app.post("/api/ideas/{idea_id}/backtest")
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

    @app.post("/api/ideas/{idea_id}/paper")
    def start_idea_paper(idea_id: str):
        """Start a paper trade for an idea."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        result = mgr.start_paper_trade(idea_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=400)
        return result

    @app.post("/api/ideas/{idea_id}/paper/close")
    async def close_idea_paper(idea_id: str, request: Request):
        """Close a paper trade."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        ct = request.headers.get("content-type", "")
        if "json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        reason = body.get("reason", "")
        result = mgr.close_paper_trade(idea_id, reason=reason)
        if not result.get("success"):
            return JSONResponse(result, status_code=400)
        return result

    @app.get("/api/ideas/{idea_id}/paper/status")
    def idea_paper_status(idea_id: str):
        """Get paper trade P&L status."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        return mgr.get_paper_trade_status(idea_id)

    @app.post("/api/ideas/{idea_id}/notes")
    async def update_idea_notes(idea_id: str, request: Request):
        """Add user notes to an idea."""
        from data.trade_db import update_trade_idea
        ct = request.headers.get("content-type", "")
        if "json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
        notes = body.get("notes", "")
        idea = get_trade_idea(idea_id)
        if not idea:
            return JSONResponse({"error": "Idea not found"}, status_code=404)
        existing = idea.get("user_notes") or ""
        new_notes = f"{existing}\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {notes}".strip()
        update_trade_idea(idea_id, user_notes=new_notes)
        return {"success": True}

    @app.post("/api/ideas/backfill")
    def backfill_ideas():
        """Backfill trade ideas from existing council analyses."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager()
        count = mgr.backfill_ideas_from_events()
        return {"success": True, "created": count}

    @app.get("/api/ideas/{idea_id}/research")
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

    @app.post("/api/ideas/{idea_id}/research/start")
    def start_idea_research(idea_id: str):
        """Manually trigger research for an idea."""
        from intelligence.idea_research import IdeaResearcher
        idea = get_trade_idea(idea_id)
        if not idea:
            return JSONResponse({"error": "Idea not found"}, status_code=404)
        researcher = IdeaResearcher()
        job_id = researcher.run_async(idea_id)
        return {"success": True, "job_id": job_id, "idea_id": idea_id}

    # ── Idea Pipeline HTMX fragments ────────────────────────────────────

    @app.get("/fragments/idea-actions/{idea_id}", response_class=HTMLResponse)
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

    @app.get("/fragments/idea-detail/{idea_id}", response_class=HTMLResponse)
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

    @app.get("/fragments/idea-pipeline-board", response_class=HTMLResponse)
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

    @app.get("/fragments/pipeline-status", response_class=HTMLResponse)
    def pipeline_status_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_pipeline_status.html",
            {"request": request, "pipeline": control.pipeline_status()},
        )

    @app.get("/fragments/reconcile-report", response_class=HTMLResponse)
    def reconcile_report_fragment(request: Request):
        report = control.reconcile_report().get("report", {})
        return TEMPLATES.TemplateResponse(
            request,
            "_reconcile_report.html",
            {"request": request, "report": report},
        )

    @app.get("/fragments/research", response_class=HTMLResponse)
    def research_fragment(request: Request):
        calibration_runs = get_calibration_runs(limit=20)
        latest_calibration_run_id = calibration_runs[0]["id"] if calibration_runs else ""
        return TEMPLATES.TemplateResponse(
            request,
            "_research.html",
            {
                "request": request,
                "option_summary": get_option_contract_summary(),
                "option_contracts": get_option_contracts(limit=40),
                "calibration_runs": calibration_runs,
                "latest_calibration_run_id": latest_calibration_run_id,
                "strategy_sets": get_strategy_parameter_sets(
                    limit=20,
                    strategy_key="ibs_credit_spreads",
                ),
                "strategy_promotions": get_strategy_promotions(
                    limit=20,
                    strategy_key="ibs_credit_spreads",
                ),
                "active_shadow_set": get_active_strategy_parameter_set(
                    "ibs_credit_spreads", status="shadow"
                ),
                "active_staged_set": get_active_strategy_parameter_set(
                    "ibs_credit_spreads", status="staged_live"
                ),
                "active_live_set": get_active_strategy_parameter_set(
                    "ibs_credit_spreads", status="live"
                ),
                "promotion_gate": build_promotion_gate_report("ibs_credit_spreads"),
            },
        )

    @app.get("/fragments/signal-engine", response_class=HTMLResponse)
    def signal_engine_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_signal_engine.html",
            {
                "request": request,
                "signal_shadow": enrich_signal_shadow_payload(get_signal_shadow_report()),
            },
        )

    @app.get("/fragments/execution-quality", response_class=HTMLResponse)
    def execution_quality_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_execution_quality.html",
            {
                "request": request,
                "eq": get_execution_quality_payload(days=30),
            },
        )

    @app.get("/fragments/portfolio-analytics", response_class=HTMLResponse)
    def portfolio_analytics_fragment(request: Request, days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS):
        return TEMPLATES.TemplateResponse(
            request,
            "_portfolio_analytics.html",
            {
                "request": request,
                "analytics": build_portfolio_analytics_payload(days=days),
            },
        )

    @app.get("/fragments/promotion-gate", response_class=HTMLResponse)
    def promotion_gate_fragment(
        request: Request,
        strategy_key: str = "ibs_credit_spreads",
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

    @app.get("/fragments/calibration-run", response_class=HTMLResponse)
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

    @app.get("/fragments/log-tail", response_class=HTMLResponse)
    def log_tail_fragment(request: Request):
        try:
            log_text = _tail_file(control.process_log, lines=120)
        except FileNotFoundError:
            log_text = ""
        return TEMPLATES.TemplateResponse(
            request,
            "_log_tail.html",
            {"request": request, "log_text": log_text},
        )

    @app.get("/api/stream/events")
    async def events_stream():
        async def event_generator():
            last_id = None
            while True:
                latest = get_bot_events(limit=1)
                if latest:
                    event = latest[0]
                    event_id = event.get("id")
                    if event_id != last_id:
                        last_id = event_id
                        payload = json.dumps(event)
                        yield f"event: bot_event\ndata: {payload}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # ── O-008: Backtester control-plane surface ──────────────────────────────

    @app.post("/api/backtest")
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

    @app.get("/api/backtest/{job_id}")
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

    @app.get("/fragments/backtest", response_class=HTMLResponse)
    def backtest_fragment(request: Request):
        """Fragment showing recent backtest runs."""
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

    # ─── Intelligence webhooks ─────────────────────────────────────────

    @app.get("/api/webhooks/sa_debug_ping")
    async def sa_debug_ping(
        stage: str = "",
        v: str = "",
        href: str = "",
        host: str = "",
        page_type: str = "",
    ):
        """Lightweight debug beacon for bookmarklet execution tracing."""
        _safe_log_event(
            category="DEBUG",
            headline=f"SA bookmarklet ping: {stage or 'unknown'}",
            detail=(
                f"v={v or '-'}, host={host or '-'}, page_type={page_type or '-'}, "
                f"url={href or '-'}"
            )[:500],
            ticker=None,
            strategy="sa_debug_ping",
        )
        return Response(status_code=204)

    @app.post("/api/webhooks/sa_intel")
    async def sa_intel_webhook(request: Request):
        """Receive Seeking Alpha page data from browser bookmarklet.

        Expects JSON with: title, content, url, tickers (optional), author (optional).
        Runs LLM council analysis in background and returns job ID.
        """
        try:
            body = await request.body()
            if len(body) > 256_000:
                return JSONResponse(
                    {"ok": False, "error": "payload_too_large"},
                    status_code=413,
                )
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                {"ok": False, "error": "invalid_payload"},
                status_code=400,
            )

        content = str(payload.get("content") or payload.get("text") or "").strip()
        if not content:
            return JSONResponse(
                {"ok": False, "error": "missing_content", "detail": "No content field in payload."},
                status_code=422,
            )

        tickers_raw = payload.get("tickers") or []
        if isinstance(tickers_raw, str):
            tickers_raw = [t.strip() for t in tickers_raw.split(",") if t.strip()]

        submission = IntelSubmission(
            source="seeking_alpha",
            content=content,
            url=str(payload.get("url", "")),
            title=str(payload.get("title", "")),
            author=str(payload.get("author", "")),
            tickers=tickers_raw,
            metadata={"sa_rating": payload.get("rating"), "sa_grades": payload.get("grades")},
        )

        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="intel_analysis", status="queued",
                    detail=f"SA intel: {submission.title[:80]}")
        analyze_intel_async(submission, job_id)

        _safe_log_event(
            category="SIGNAL",
            headline=f"SA intel received: {submission.title[:60]}",
            detail=f"url={submission.url}, tickers={','.join(submission.tickers[:5])}",
            ticker=submission.tickers[0] if submission.tickers else None,
            strategy="sa_intel",
        )

        return {"ok": True, "job_id": job_id, "message": "SA intel queued for LLM analysis."}

    @app.post("/api/webhooks/sa_quant_capture")
    async def sa_quant_capture_webhook(request: Request):
        """Receive structured SA quant data captured from the user's browser."""
        try:
            body = await request.body()
            if len(body) > 128_000:
                return JSONResponse(
                    {"ok": False, "error": "payload_too_large"},
                    status_code=413,
                )
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                {"ok": False, "error": "invalid_payload"},
                status_code=400,
            )

        try:
            capture = parse_sa_browser_payload(payload)
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "error": "invalid_capture", "detail": str(exc)},
                status_code=422,
            )

        retrieved_at = _utc_now_iso()
        capture_source = str(
            capture.snapshot.raw_fields.get("source")
            or payload.get("source")
            or SA_BROWSER_CAPTURE_SOURCE
        ).strip() or SA_BROWSER_CAPTURE_SOURCE
        stored_feature_count, layer_score_payload = _store_sa_browser_capture(
            capture,
            retrieved_at=retrieved_at,
            capture_source=capture_source,
            log_strategy="sa_quant_capture",
        )

        return {
            "ok": True,
            "ticker": capture.ticker,
            "rating": capture.snapshot.rating,
            "quant_score": capture.snapshot.quant_score_raw,
            "factor_grades": capture.factor_grades,
            "feature_count": stored_feature_count,
            "layer_score": layer_score_payload,
            "message": "SA browser capture stored.",
        }

    @app.post("/api/webhooks/sa_symbol_capture")
    async def sa_symbol_capture_webhook(request: Request):
        """Receive a full Seeking Alpha symbol snapshot captured in-browser."""
        try:
            body = await request.body()
            if len(body) > 1_500_000:
                return JSONResponse(
                    {"ok": False, "error": "payload_too_large"},
                    status_code=413,
                )
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                {"ok": False, "error": "invalid_payload"},
                status_code=400,
            )

        if not isinstance(payload, dict):
            return JSONResponse(
                {"ok": False, "error": "invalid_capture", "detail": "payload must be an object"},
                status_code=422,
            )

        summary_payload = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        summary_payload = dict(summary_payload)
        for key in (
            "ticker",
            "url",
            "title",
            "page_type",
            "captured_at",
            "source",
            "source_ref",
            "bookmarklet_version",
            "rating",
            "quant_score",
            "author_rating",
            "wall_st_rating",
            "grades",
            "sa_history",
            "scan_debug",
        ):
            if key not in summary_payload and payload.get(key) not in (None, "", [], {}):
                summary_payload[key] = payload.get(key)

        capture_source = str(
            summary_payload.get("source")
            or payload.get("source")
            or SA_NETWORK_CAPTURE_SOURCE
        ).strip() or SA_NETWORK_CAPTURE_SOURCE
        sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
        raw_responses = payload.get("raw_responses") if isinstance(payload.get("raw_responses"), list) else []
        raw_fields = (
            dict(summary_payload.get("raw_fields"))
            if isinstance(summary_payload.get("raw_fields"), dict)
            else {}
        )
        raw_fields.setdefault("source", capture_source)
        raw_fields.setdefault("section_names", sorted(str(key) for key in sections.keys()))
        raw_fields.setdefault("raw_response_count", len(raw_responses))
        summary_payload["raw_fields"] = raw_fields

        normalized_snapshot = normalize_sa_symbol_snapshot(
            {
                **payload,
                "summary": summary_payload,
                "sections": sections,
                "raw_responses": raw_responses,
            }
        )
        summary_payload = (
            dict(normalized_snapshot.get("summary"))
            if isinstance(normalized_snapshot.get("summary"), dict)
            else summary_payload
        )
        normalized_sections = (
            dict(normalized_snapshot.get("normalized_sections"))
            if isinstance(normalized_snapshot.get("normalized_sections"), dict)
            else {}
        )
        if normalized_sections:
            summary_payload["normalized_sections"] = normalized_sections
            raw_fields = (
                dict(summary_payload.get("raw_fields"))
                if isinstance(summary_payload.get("raw_fields"), dict)
                else {}
            )
            raw_fields["normalized_section_names"] = sorted(str(key) for key in normalized_sections.keys())
            summary_payload["raw_fields"] = raw_fields

        symbol = str(summary_payload.get("ticker") or payload.get("ticker") or "").strip().upper()
        if not symbol:
            match = _re.search(
                r"/symbol/([A-Z.=\-]+)",
                str(summary_payload.get("url") or payload.get("url") or ""),
                _re.IGNORECASE,
            )
            if match:
                symbol = match.group(1).upper()
        if not symbol:
            return JSONResponse(
                {"ok": False, "error": "invalid_capture", "detail": "ticker is required"},
                status_code=422,
            )

        retrieved_at = _utc_now_iso()
        symbol_payload = dict(payload)
        symbol_payload["summary"] = summary_payload
        symbol_payload["normalized_sections"] = normalized_sections
        store = EventStore()
        store.write_event(
            EventRecord(
                event_type=SA_SYMBOL_CAPTURE_EVENT_TYPE,
                source=capture_source,
                source_ref=str(
                    payload.get("source_ref")
                    or summary_payload.get("source_ref")
                    or summary_payload.get("url")
                    or payload.get("url")
                    or f"sa-symbol-{symbol}"
                ).strip(),
                retrieved_at=retrieved_at,
                event_timestamp=str(
                    payload.get("captured_at")
                    or summary_payload.get("captured_at")
                    or retrieved_at
                ).strip()
                or retrieved_at,
                symbol=symbol,
                headline=f"Seeking Alpha symbol capture: {symbol}",
                detail=(
                    f"sections={len(sections)}, "
                    f"normalized_sections={len(normalized_sections)}, "
                    f"raw_responses={len(raw_responses)}, "
                    f"has_summary={bool(summary_payload)}"
                ),
                confidence=0.99,
                provenance_descriptor={
                    "ticker": symbol,
                    "url": summary_payload.get("url") or payload.get("url") or "",
                    "page_type": "symbol",
                    "capture_source": capture_source,
                    "section_names": sorted(
                        {*(str(key) for key in sections.keys()), *(str(key) for key in normalized_sections.keys())}
                    )[:20],
                },
                payload=symbol_payload,
            )
        )

        capture = None
        stored_feature_count = 0
        layer_score_payload: Optional[dict[str, Any]] = None
        try:
            capture = parse_sa_browser_payload(summary_payload)
        except ValueError:
            capture = None

        if capture is not None:
            capture_source = str(
                capture.snapshot.raw_fields.get("source") or capture_source
            ).strip() or capture_source
            stored_feature_count, layer_score_payload = _store_sa_browser_capture(
                capture,
                retrieved_at=retrieved_at,
                capture_source=capture_source,
                log_strategy="sa_symbol_capture",
            )
        else:
            _safe_log_event(
                category="SIGNAL",
                headline=f"SA symbol snapshot received: {symbol}",
                detail=(
                    f"source={capture_source}, "
                    f"sections={len(sections)}, "
                    f"normalized_sections={len(normalized_sections)}, "
                    f"raw_responses={len(raw_responses)}"
                ),
                ticker=symbol,
                strategy="sa_symbol_capture",
            )

        return {
            "ok": True,
            "ticker": symbol,
            "section_count": len(sections),
            "normalized_section_count": len(normalized_sections),
            "raw_response_count": len(raw_responses),
            "rating": capture.snapshot.rating if capture is not None else "",
            "quant_score": capture.snapshot.quant_score_raw if capture is not None else None,
            "factor_grades": capture.factor_grades if capture is not None else {},
            "feature_count": stored_feature_count,
            "layer_score": layer_score_payload,
            "message": "SA symbol capture stored.",
        }

    @app.post("/api/webhooks/x_intel")
    async def x_intel_webhook(request: Request):
        """Receive X/Twitter content for LLM analysis.

        Expects JSON with: content (tweet/thread text), url (optional),
        author (optional), tickers (optional).
        """
        try:
            body = await request.body()
            if len(body) > 256_000:
                return JSONResponse(
                    {"ok": False, "error": "payload_too_large"},
                    status_code=413,
                )
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                {"ok": False, "error": "invalid_payload"},
                status_code=400,
            )

        content = str(payload.get("content") or payload.get("text") or "").strip()
        if not content:
            return JSONResponse(
                {"ok": False, "error": "missing_content", "detail": "No content field in payload."},
                status_code=422,
            )

        tickers_raw = payload.get("tickers") or []
        if isinstance(tickers_raw, str):
            tickers_raw = [t.strip() for t in tickers_raw.split(",") if t.strip()]

        submission = IntelSubmission(
            source="x_twitter",
            content=content,
            url=str(payload.get("url", "")),
            title=str(payload.get("title") or payload.get("author", "")),
            author=str(payload.get("author", "")),
            tickers=tickers_raw,
        )

        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="intel_analysis", status="queued",
                    detail=f"X intel: {submission.title[:80]}")
        analyze_intel_async(submission, job_id)

        _safe_log_event(
            category="SIGNAL",
            headline=f"X intel received: {submission.author or 'unknown'}",
            detail=f"url={submission.url}, content_len={len(content)}",
            strategy="x_intel",
        )

        return {"ok": True, "job_id": job_id, "message": "X intel queued for LLM analysis."}

    @app.post("/api/webhooks/telegram")
    async def telegram_webhook(request: Request):
        """Receive Telegram bot updates (forwarded messages, commands).

        Set up via: POST https://api.telegram.org/bot<TOKEN>/setWebhook?url=<YOUR_URL>/api/webhooks/telegram
        """
        try:
            payload = json.loads((await request.body()).decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"ok": False}, status_code=400)

        message = payload.get("message") or payload.get("channel_post") or {}
        text = str(message.get("text") or message.get("caption") or "").strip()
        chat_id = message.get("chat", {}).get("id")

        if not text or not chat_id:
            return {"ok": True}  # Acknowledge but ignore non-text updates

        expected_chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if expected_chat and str(chat_id) != expected_chat:
            return {"ok": True}  # Ignore messages from unknown chats

        # Check if it's an X/Twitter link or forwarded content
        is_x_content = any(domain in text for domain in [
            "twitter.com/", "x.com/", "nitter.", "vxtwitter.com/",
        ])

        # Extract URL if present
        urls = _re.findall(r'https?://\S+', text)
        url = urls[0] if urls else ""

        if is_x_content or text.startswith("/analyze"):
            # Strip /analyze command prefix if present
            content = text.replace("/analyze", "", 1).strip() if text.startswith("/analyze") else text

            # If it's an X link, fetch the full tweet via API
            if is_x_content and url:
                logger.info("Fetching tweet for URL: %s (X_CONSUMER_KEY set: %s)", url, bool(config.X_CONSUMER_KEY))
                try:
                    tweet_data = _fetch_tweet_from_url(url)
                    logger.info("Tweet fetch result: %s", "success" if tweet_data else "failed/None")
                except Exception as exc:
                    logger.error("Tweet fetch EXCEPTION: %s", exc, exc_info=True)
                    tweet_data = None
                if tweet_data:
                    content = tweet_data["text"]
                    if tweet_data.get("author"):
                        content = f"@{tweet_data['author']}: {content}"
                    if tweet_data.get("created_at"):
                        content += f"\n\n[Posted: {tweet_data['created_at']}]"

            submission = IntelSubmission(
                source="x_twitter" if is_x_content else "telegram",
                content=content,
                url=url,
                title=f"Forwarded via Telegram",
            )

            job_id = str(uuid.uuid4())
            create_job(job_id=job_id, job_type="intel_analysis", status="queued",
                        detail=f"Telegram intel: {content[:80]}")
            analyze_intel_async(submission, job_id)

            # Reply to the user
            _telegram_reply(chat_id, f"Queued for LLM analysis (job {job_id[:8]})")
        elif text == "/status":
            status = control.status()
            mode = status.get("mode", "unknown")
            _telegram_reply(chat_id, f"Bot: {mode}\nKill switch: {'ON' if status.get('kill_switch_active') else 'off'}")
        elif text == "/help":
            _telegram_reply(
                chat_id,
                "Commands:\n"
                "/analyze <text> - Analyze content for trade ideas\n"
                "/status - Bot status\n"
                "Forward X/Twitter links to auto-analyze\n"
                "Paste any text to analyze",
            )
        else:
            # Treat any other text as content to analyze
            submission = IntelSubmission(
                source="telegram",
                content=text,
                url=url,
                title="Telegram message",
            )
            job_id = str(uuid.uuid4())
            create_job(job_id=job_id, job_type="intel_analysis", status="queued",
                        detail=f"Telegram intel: {text[:80]}")
            analyze_intel_async(submission, job_id)
            _telegram_reply(chat_id, f"Analyzing... (job {job_id[:8]})")

        return {"ok": True}

    @app.get("/api/intel/history")
    def intel_history(limit: int = 20):
        """List recent intel analysis results."""
        from intelligence.event_store import EventStore
        store = EventStore()
        events = store.list_events(limit=limit, event_type="intel_analysis")
        return {"ok": True, "count": len(events), "events": events}

    @app.get("/intel/bookmarklet", response_class=HTMLResponse)
    def bookmarklet_install(request: Request):
        """Page to install the SA bookmarklet."""
        host = request.headers.get("host", "localhost:8000")
        scheme = request.headers.get("x-forwarded-proto", "https")
        endpoint = f"{scheme}://{host}"
        js_path = PROJECT_ROOT / "app" / "web" / "static" / "sa_bookmarklet.js"
        try:
            js_src = js_path.read_text()
        except FileNotFoundError:
            js_src = "alert('Bookmarklet file not found');"
        bookmarklet_version = _extract_bookmarklet_version(js_src)
        bookmarklet_url = html.escape(_build_bookmarklet_href(js_src, endpoint), quote=True)
        html_body = (
            "<html><head><title>BoxRoomCapital Bookmarklet</title>"
            "<style>body{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:40px}"
            "a{color:#00ff88;font-size:18px;padding:12px 24px;border:2px solid #00ff88;"
            "text-decoration:none;border-radius:6px;display:inline-block}"
            "a:hover{background:#00ff8822}code{background:#161b22;padding:2px 6px;border-radius:3px}"
            "h1{color:#00ff88}h2{color:#888;margin-top:30px}.meta{color:#8b949e;margin:8px 0 20px 0}"
            ".instructions{max-width:600px;line-height:1.6}</style></head>"
            "<body><h1>BoxRoomCapital Seeking Alpha Bookmarklet</h1>"
            "<div class='instructions'>"
            f"<p class='meta'>Bookmarklet version: <code>{html.escape(bookmarklet_version)}</code></p>"
            "<h2>Install</h2>"
            f"<p>Drag this link to your bookmarks bar:</p>"
            f'<p><a href="{bookmarklet_url}">Send to BRC</a></p>'
            "<h2>Usage</h2>"
            "<ol><li>Browse to any Seeking Alpha article or stock page</li>"
            "<li>Click the <code>Send to BRC</code> bookmark</li>"
            "<li>Article pages are sent to the LLM council for analysis</li>"
            "<li>Stock pages with quant data are stored as SA browser captures for the signal engine</li>"
            "<li>Results appear in Intel History and the research event store</li></ol>"
            f"<h2>Server</h2><p>Endpoint: <code>{endpoint}</code></p>"
            "</div></body></html>"
        )
        return HTMLResponse(
            content=html_body,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    return app


def _get_x_oauth() -> "OAuth1Session | None":
    """Create an authenticated X API OAuth1 session, or None if unconfigured."""
    ck = config.X_CONSUMER_KEY
    cs = config.X_CONSUMER_SECRET
    at = config.X_ACCESS_TOKEN
    ats = config.X_ACCESS_TOKEN_SECRET
    # Lazy reload: if credentials are empty, try reloading .env in case it was
    # created after the process started (common in Replit).
    if not all([ck, cs, at, ats]):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)
        ck = os.getenv("X_CONSUMER_KEY", "")
        cs = os.getenv("X_CONSUMER_SECRET", "")
        at = os.getenv("X_ACCESS_TOKEN", "")
        ats = os.getenv("X_ACCESS_TOKEN_SECRET", "")
        # Update config module so subsequent calls don't need to reload
        config.X_CONSUMER_KEY = ck
        config.X_CONSUMER_SECRET = cs
        config.X_ACCESS_TOKEN = at
        config.X_ACCESS_TOKEN_SECRET = ats
    if not all([ck, cs, at, ats]):
        logger.warning("X API credentials not configured (checked .env and env vars)")
        return None
    from requests_oauthlib import OAuth1Session
    return OAuth1Session(ck, client_secret=cs, resource_owner_key=at, resource_owner_secret=ats)


def _fetch_single_tweet(oauth, tweet_id: str) -> dict | None:
    """Fetch a single tweet with full metadata."""
    resp = oauth.get(
        f"https://api.x.com/2/tweets/{tweet_id}",
        params={
            "tweet.fields": "text,author_id,created_at,conversation_id,referenced_tweets,note_tweet,attachments",
            "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
            "media.fields": "type,url,alt_text",
            "user.fields": "username,name",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        logger.warning("X API returned %d: %s", resp.status_code, resp.text[:200])
        return None
    return resp.json()


def _resolve_author(data: dict, author_id: str) -> str:
    """Extract username from includes.users."""
    for user in data.get("includes", {}).get("users", []):
        if user.get("id") == author_id:
            return user.get("username", "")
    return ""


def _get_tweet_text(data: dict) -> str:
    """Get full tweet text, preferring note_tweet (long-form) over regular text."""
    tweet = data.get("data", {})
    # note_tweet contains the full text for tweets > 280 chars and X Articles
    note = tweet.get("note_tweet", {})
    if note and note.get("text"):
        return note["text"]
    return tweet.get("text", "")


def _describe_media(data: dict) -> str:
    """Summarize attached media from includes."""
    media_list = data.get("includes", {}).get("media", [])
    if not media_list:
        return ""
    descriptions = []
    for m in media_list:
        mtype = m.get("type", "unknown")
        alt = m.get("alt_text", "")
        if alt:
            descriptions.append(f"[{mtype}: {alt}]")
        else:
            descriptions.append(f"[{mtype} attached]")
    return "\n".join(descriptions)


def _fetch_thread(oauth, conversation_id: str, author_username: str) -> list[str]:
    """Fetch all tweets in a thread by the same author (recent threads only)."""
    try:
        resp = oauth.get(
            "https://api.x.com/2/tweets/search/recent",
            params={
                "query": f"conversation_id:{conversation_id} from:{author_username}",
                "tweet.fields": "text,created_at,note_tweet",
                "max_results": 100,
                "sort_order": "recency",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        tweets = resp.json().get("data", [])
        # Reverse to chronological order (API returns newest first)
        tweets.reverse()
        parts = []
        for t in tweets:
            note = t.get("note_tweet", {})
            text = note.get("text") if note and note.get("text") else t.get("text", "")
            parts.append(text)
        return parts
    except Exception as e:
        logger.warning("Thread fetch failed: %s", e)
        return []


def _fetch_tweet_from_url(url: str) -> dict | None:
    """Fetch full tweet text from an X/Twitter URL using the v2 API.

    Handles: threads, retweets, long tweets (note_tweet), and media attachments.
    """
    match = _re.search(r'(?:twitter\.com|x\.com)/.+/status/(\d+)', url)
    if not match:
        return None

    tweet_id = match.group(1)
    oauth = _get_x_oauth()
    if not oauth:
        return None

    try:
        data = _fetch_single_tweet(oauth, tweet_id)
        if not data or "data" not in data:
            return None

        tweet = data["data"]
        author_id = tweet.get("author_id", "")
        author = _resolve_author(data, author_id)
        created_at = tweet.get("created_at", "")
        conversation_id = tweet.get("conversation_id", "")

        # Handle retweets — fetch the original tweet for full text
        ref_tweets = tweet.get("referenced_tweets", [])
        retweeted_id = None
        for ref in ref_tweets:
            if ref.get("type") == "retweeted":
                retweeted_id = ref.get("id")
                break

        if retweeted_id:
            orig_data = _fetch_single_tweet(oauth, retweeted_id)
            if orig_data and "data" in orig_data:
                orig_tweet = orig_data["data"]
                orig_author = _resolve_author(orig_data, orig_tweet.get("author_id", ""))
                text = _get_tweet_text(orig_data)
                media_desc = _describe_media(orig_data)
                if media_desc:
                    text += f"\n\n{media_desc}"
                # Check if original is a thread
                orig_conv_id = orig_tweet.get("conversation_id", "")
                if orig_conv_id and orig_author:
                    thread_parts = _fetch_thread(oauth, orig_conv_id, orig_author)
                    if len(thread_parts) > 1:
                        text = "\n\n---\n\n".join(thread_parts)
                        if media_desc:
                            text += f"\n\n{media_desc}"
                return {
                    "text": f"RT @{orig_author}: {text}" if orig_author else text,
                    "author": author,
                    "created_at": created_at,
                    "tweet_id": tweet_id,
                }

        # Get full text (handles note_tweet / X Articles)
        text = _get_tweet_text(data)
        media_desc = _describe_media(data)
        if media_desc:
            text += f"\n\n{media_desc}"

        # Check if this is part of a thread
        if conversation_id and author:
            thread_parts = _fetch_thread(oauth, conversation_id, author)
            if len(thread_parts) > 1:
                text = "\n\n---\n\n".join(thread_parts)
                if media_desc:
                    text += f"\n\n{media_desc}"

        return {
            "text": text,
            "author": author,
            "created_at": created_at,
            "tweet_id": tweet_id,
        }
    except Exception as e:
        logger.warning("Failed to fetch tweet %s: %s", tweet_id, e)
        return None


def _telegram_reply(chat_id: int, text: str) -> None:
    """Send a reply to a Telegram chat."""
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


def action_message(text: str, ok: bool) -> str:
    css = "action-msg ok" if ok else "action-msg error"
    return f"<div class='{css}'>{text}</div>"


def _is_test_artifact_incident(item: Optional[dict[str, Any]]) -> bool:
    """Hide FastAPI TestClient-generated incidents from operator-facing UI."""
    if not item:
        return False
    detail = item.get("detail")
    if not isinstance(detail, str):
        return False
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return payload.get("client_ip") == "testclient"


def _visible_incidents(limit: int = 25) -> list[dict[str, Any]]:
    """Return incidents intended for operators, excluding test artifacts."""
    raw_incidents = get_incidents(limit=max(limit * 4, limit))
    visible: list[dict[str, Any]] = []
    for incident in raw_incidents:
        if _is_test_artifact_incident(incident):
            continue
        visible.append(incident)
        if len(visible) >= limit:
            break
    return visible


def _safe_log_event(**kwargs: Any) -> None:
    """Best-effort event logging for non-critical API paths."""
    try:
        log_event(**kwargs)
    except Exception:
        return


def build_status_payload() -> dict[str, Any]:
    return {
        "engine": control.status(),
        "summary": get_summary(),
        "open_option_positions": get_open_option_positions(),
    }


def _unavailable_risk_briefing_payload(
    message: str,
    action: str,
    code: str = "RISK_DATA_UNAVAILABLE",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ok": False,
        "generated_at": now,
        "state": "unavailable",
        "summary": {
            "fund_nav": None,
            "day_pnl": None,
            "drawdown_pct": None,
            "gross_exposure_pct": None,
            "net_exposure_pct": None,
            "cash_buffer_pct": None,
            "open_risk_pct": None,
        },
        "limits": [],
        "alerts": [
            {
                "severity": "warn",
                "code": code,
                "message": message,
                "action": action,
            }
        ],
    }


def build_risk_briefing_payload() -> dict[str, Any]:
    """Build risk briefing payload for operator surfaces from B-003 providers."""
    try:
        nav = calculate_fund_nav()
        if nav.total_nav <= 0 and nav.total_cash <= 0 and nav.total_positions_value <= 0:
            return _unavailable_risk_briefing_payload(
                message="No ledger data available yet.",
                action="Sync broker cash/positions and reload.",
            )

        briefing = get_risk_briefing(
            total_nav=nav.total_nav,
            daily_return_pct=nav.daily_return_pct,
            drawdown_pct=nav.drawdown_pct,
            total_cash=nav.total_cash,
            snapshot_date=nav.report_date,
        )

        status = str(briefing.get("status") or "GREEN").upper()
        state = {
            "GREEN": "ok",
            "AMBER": "attention",
            "RED": "critical",
        }.get(status, "attention")

        alerts = []
        for item in briefing.get("alerts", []):
            severity = str(item.get("severity") or "info").lower()
            if severity in {"warning", "warn", "amber"}:
                mapped = "warn"
            elif severity in {"critical", "error", "red"}:
                mapped = "critical"
            else:
                mapped = "info"
            alerts.append(
                {
                    "severity": mapped,
                    "code": item.get("code", ""),
                    "message": item.get("message", ""),
                    "action": item.get("action", ""),
                }
            )

        return {
            "ok": True,
            "generated_at": briefing.get("generated_at", datetime.now(timezone.utc).isoformat()),
            "state": state,
            "summary": {
                "fund_nav": briefing.get("fund_nav"),
                "day_pnl": briefing.get("day_pnl"),
                "drawdown_pct": briefing.get("drawdown_pct"),
                "gross_exposure_pct": briefing.get("gross_exposure_pct"),
                "net_exposure_pct": briefing.get("net_exposure_pct"),
                "cash_buffer_pct": briefing.get("cash_buffer_pct"),
                "open_risk_pct": briefing.get("open_risk_pct"),
            },
            "limits": briefing.get("limits", []),
            "alerts": alerts,
        }
    except Exception:
        return _unavailable_risk_briefing_payload(
            message="Risk briefing provider failed.",
            action="Check risk/nav services and retry.",
            code="RISK_DATA_ERROR",
        )


def build_portfolio_analytics_payload(days: int = config.PORTFOLIO_ANALYTICS_DEFAULT_DAYS) -> dict[str, Any]:
    """Build portfolio analytics payload from fund daily NAV history."""
    bounded_days = max(7, min(int(days), int(config.PORTFOLIO_ANALYTICS_MAX_DAYS)))
    rows = get_fund_daily_reports(days=bounded_days)
    ordered = sorted(
        [r for r in rows if r.get("report_date") and r.get("total_nav") is not None],
        key=lambda r: str(r["report_date"]),
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    if len(ordered) < 2:
        return {
            "ok": False,
            "generated_at": generated_at,
            "days": bounded_days,
            "points": len(ordered),
            "latest_nav": float(ordered[-1]["total_nav"]) if ordered else None,
            "metrics": {},
            "drawdowns": [],
            "rolling": {"window": config.PORTFOLIO_ANALYTICS_ROLLING_WINDOW, "dates": [], "rolling_return_pct": [], "rolling_volatility_pct": [], "rolling_sharpe": []},
            "message": "Insufficient fund history for analytics.",
        }

    dates = [str(r["report_date"]) for r in ordered]
    equity_curve = [float(r["total_nav"]) for r in ordered]
    returns: list[float] = []
    for idx in range(1, len(equity_curve)):
        prev = equity_curve[idx - 1]
        curr = equity_curve[idx]
        if prev <= 0:
            returns.append(0.0)
        else:
            returns.append((curr / prev) - 1.0)

    metrics = compute_metrics(
        returns=returns,
        periods_per_year=252.0,
        risk_free_rate=float(config.PORTFOLIO_ANALYTICS_RISK_FREE_RATE),
    ).to_dict()
    drawdowns = [
        {
            "start_idx": d.start_idx,
            "trough_idx": d.trough_idx,
            "end_idx": d.end_idx,
            "depth_pct": d.depth_pct,
            "duration_bars": d.duration_bars,
            "recovery_bars": d.recovery_bars,
            "start_date": dates[d.start_idx] if 0 <= d.start_idx < len(dates) else "",
            "trough_date": dates[d.trough_idx] if 0 <= d.trough_idx < len(dates) else "",
            "end_date": dates[d.end_idx] if 0 <= d.end_idx < len(dates) else "",
        }
        for d in compute_drawdowns(equity_curve, top_n=3)
    ]

    rolling_window = min(
        int(config.PORTFOLIO_ANALYTICS_ROLLING_WINDOW),
        len(returns) if returns else int(config.PORTFOLIO_ANALYTICS_ROLLING_WINDOW),
    )
    rolling = compute_rolling_stats(
        returns=returns,
        window=max(5, rolling_window),
        periods_per_year=252.0,
        dates=dates[1:],
    )

    return {
        "ok": True,
        "generated_at": generated_at,
        "days": bounded_days,
        "points": len(ordered),
        "latest_nav": equity_curve[-1],
        "latest_daily_return_pct": round(returns[-1] * 100.0, 4) if returns else 0.0,
        "metrics": metrics,
        "drawdowns": drawdowns,
        "rolling": {
            "window": rolling.window,
            "dates": rolling.dates,
            "rolling_return_pct": rolling.rolling_return_pct,
            "rolling_volatility_pct": rolling.rolling_volatility_pct,
            "rolling_sharpe": rolling.rolling_sharpe,
        },
        "series": [{"date": d, "nav": n} for d, n in zip(dates, equity_curve)],
    }


def _page_context(request: Request, page_key: str, title: str) -> dict[str, Any]:
    payload = build_status_payload()
    return {
        "request": request,
        "title": title,
        "page_key": page_key,
        "status": payload["engine"],
        "summary": payload["summary"],
        "open_positions": payload["open_option_positions"],
        "default_mode": config.TRADING_MODE,
    }


def _run_scan_job(job_id: str, mode: str):
    update_job(job_id, status="running", detail=f"Running one-shot scan ({mode.upper()})")
    try:
        result = control.scan_once(mode=mode)
    except Exception as exc:
        update_job(job_id, status="failed", detail="Scan crashed", error=str(exc))
        return
    if result["ok"]:
        update_job(
            job_id,
            status="completed",
            detail=result["message"],
            result=result.get("stdout_tail", ""),
        )
        return

    update_job(
        job_id,
        status="failed",
        detail=result["message"],
        result=result.get("stdout_tail", ""),
        error=result.get("stderr_tail", ""),
    )


def _run_reconcile_job(job_id: str):
    update_job(job_id, status="running", detail="Running reconcile")
    try:
        result = control.reconcile()
    except Exception as exc:
        update_job(job_id, status="failed", detail="Reconcile crashed", error=str(exc))
        return
    if result["ok"]:
        update_job(job_id, status="completed", detail=result["message"])
        return
    update_job(job_id, status="failed", detail=result["message"], error=result.get("message"))


def _run_signal_shadow_job(job_id: str):
    update_job(job_id, status="running", detail="Running signal shadow cycle")
    try:
        report = run_signal_shadow_cycle()
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            detail="Signal shadow cycle failed",
            error=str(exc),
        )
        return

    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    detail = (
        f"scored={int(summary.get('tickers_scored', 0))}/"
        f"{int(summary.get('tickers_total', 0))}"
    )
    update_job(
        job_id,
        status="completed",
        detail=detail,
        result=json.dumps(report, sort_keys=True, default=str),
    )


def _run_signal_tier1_job(job_id: str):
    update_job(job_id, status="running", detail="Running tier-1 signal jobs + shadow ranking")
    try:
        outcome = run_tier1_shadow_jobs()
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            detail="Tier-1 signal shadow run failed",
            error=str(exc),
        )
        return

    report = outcome.get("shadow_report", {}) if isinstance(outcome, dict) else {}
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    ranked_count = len(outcome.get("ranked_candidates", [])) if isinstance(outcome, dict) else 0
    stale_blocked = int(summary.get("tickers_blocked_stale_layers", 0))
    missing_blocked = int(summary.get("tickers_blocked_missing_required_layers", 0))
    detail = (
        f"scored={int(summary.get('tickers_scored', 0))}/"
        f"{int(summary.get('tickers_total', 0))}, "
        f"ranked={ranked_count}, stale_blocked={stale_blocked}, "
        f"missing_blocked={missing_blocked}"
    )

    update_job(
        job_id,
        status="completed",
        detail=detail,
        result=json.dumps(outcome, sort_keys=True, default=str),
    )


def _run_close_job(job_id: str, spread_id: str, ticker: str, reason: str):
    update_job(job_id, status="running", detail="Closing spread")
    result = control.close_spread(spread_id=spread_id, ticker=ticker, reason=reason)
    if result["ok"]:
        update_job(job_id, status="completed", detail=result["message"])
        return
    update_job(job_id, status="failed", detail=result["message"], error=result.get("message"))


def _run_discovery_job(job_id: str, mode: str, details: bool, strikes: str):
    update_job(job_id, status="running", detail=f"Running options discovery ({mode})")
    search_only = (mode == "search")
    nav_only = (mode == "nav")
    result = research.run_discovery(
        search_only=search_only,
        nav_only=nav_only,
        details=details,
        strikes=strikes,
    )
    if result["ok"]:
        detail = (
            f"contracts={result.get('contracts_persisted', 0)} "
            f"search={result.get('search_count', 0)} "
            f"nav={result.get('navigation_count', 0)} "
            f"details={result.get('details_count', 0)}"
        )
        payload = json.dumps(
            {
                "output_file": result.get("output_file"),
                "contracts_persisted": result.get("contracts_persisted", 0),
                "search_count": result.get("search_count", 0),
                "navigation_count": result.get("navigation_count", 0),
                "details_count": result.get("details_count", 0),
            },
            sort_keys=True,
        )
        update_job(
            job_id,
            status="completed",
            detail=detail,
            result=payload,
        )
        return
    failure_payload = json.dumps(
        {
            "mode": mode,
            "details": details,
            "strikes": strikes,
            "message": result.get("message"),
            "hint": "Check IG credentials/session and retry.",
        },
        sort_keys=True,
    )
    update_job(
        job_id,
        status="failed",
        detail=result["message"],
        result=failure_payload,
        error=result.get("message"),
    )


def _run_calibration_job(job_id: str, index_filter: str, verbose: bool):
    scope = index_filter or "all"
    create_calibration_run(run_id=job_id, scope=scope, status="running")
    update_job(job_id, status="running", detail=f"Running calibration ({scope})")
    result = research.run_calibration(index_filter=index_filter, verbose=verbose)
    if result["ok"]:
        points = result.get("raw_quotes", []) or []
        inserted = insert_calibration_points(run_id=job_id, points=points)
        summary = result.get("summary", {}) or {}
        overall = summary.get("_overall")
        complete_calibration_run(
            run_id=job_id,
            status="completed",
            samples=inserted,
            overall_ratio=overall if isinstance(overall, (int, float)) else None,
            summary_payload=json.dumps(summary),
            error=None,
        )
        detail = (
            f"samples={result.get('samples', 0)} "
            f"stored={inserted} overall={overall if overall is not None else '-'}"
        )
        payload = json.dumps(
            {
                "output_file": result.get("output_file"),
                "samples": result.get("samples", 0),
                "stored": inserted,
                "overall_ratio": overall,
                "summary": summary,
            },
            sort_keys=True,
            default=str,
        )
        update_job(
            job_id,
            status="completed",
            detail=detail,
            result=payload,
        )
        return

    complete_calibration_run(
        run_id=job_id,
        status="failed",
        samples=0,
        overall_ratio=None,
        summary_payload=None,
        error=result.get("message"),
    )
    failure_payload = json.dumps(
        {
            "scope": scope,
            "verbose": verbose,
            "message": result.get("message"),
            "hint": "Calibration login/data fetch failed. Retry and verify IG auth + market hours.",
        },
        sort_keys=True,
    )
    update_job(
        job_id,
        status="failed",
        detail=result["message"],
        result=failure_payload,
        error=result.get("message"),
    )


def _tail_file(path: Path, lines: int = 200) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        buffer = fh.readlines()
    return "".join(buffer[-lines:])


def _parse_job_result(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _load_order_intent_store():
    try:
        from data import order_intent_store

        return order_intent_store
    except Exception:
        return None


def _safe_json_load(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _normalize_intent_item(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "intent_id": row.get("intent_id") or row.get("id") or "",
        "correlation_id": row.get("correlation_id") or "",
        "status": str(row.get("status") or "queued").lower(),
        "strategy_id": row.get("strategy_id") or row.get("action_type") or "-",
        "strategy_version": row.get("strategy_version") or "-",
        "sleeve": row.get("sleeve") or "-",
        "account_type": row.get("account_type") or "-",
        "broker_target": row.get("broker_target") or "ig",
        "instrument": row.get("instrument") or row.get("ticker") or "-",
        "updated_at": row.get("updated_at") or row.get("created_at") or "-",
        "source": source,
    }


def get_order_intent_items(limit: int = 50, status: str = "") -> list[dict[str, Any]]:
    store = _load_order_intent_store()
    if store:
        try:
            rows = store.get_order_intents(limit=limit, status=status or None)
            return [_normalize_intent_item(dict(row), source="order_intents") for row in rows]
        except Exception:
            pass

    rows = get_order_actions(limit=limit, status=status or None)
    return [_normalize_intent_item(dict(row), source="order_actions_fallback") for row in rows]


def get_order_intent_detail(intent_id: str) -> Optional[dict[str, Any]]:
    clean_id = intent_id.strip()
    if not clean_id:
        return None

    store = _load_order_intent_store()
    if store:
        try:
            item = store.get_order_intent(clean_id)
            if item:
                attempts = store.get_order_intent_attempts(clean_id)
                transitions = store.get_order_intent_transitions(clean_id)
                return {
                    "source": "order_intents",
                    "intent": _normalize_intent_item(dict(item), source="order_intents"),
                    "attempts": attempts,
                    "transitions": transitions,
                }
        except Exception:
            pass

    for row in get_order_actions(limit=500):
        if row.get("id") == clean_id or row.get("correlation_id") == clean_id:
            normalized = _normalize_intent_item(dict(row), source="order_actions_fallback")
            attempt = int(row.get("attempt", 0) or 0)
            transition = {
                "from_status": "running" if normalized["status"] in {"completed", "failed", "retrying"} else None,
                "to_status": normalized["status"],
                "attempt": attempt,
                "transition_at": row.get("updated_at") or row.get("created_at"),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
                "response_payload": _safe_json_load(row.get("result_payload")),
            }
            attempt_row = {
                "attempt": attempt,
                "status": normalized["status"],
                "updated_at": row.get("updated_at"),
                "request_payload": _safe_json_load(row.get("request_payload")),
                "response_payload": _safe_json_load(row.get("result_payload")),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
            }
            return {
                "source": "order_actions_fallback",
                "intent": normalized,
                "attempts": [attempt_row],
                "transitions": [transition],
            }
    return None


def build_broker_health_payload() -> dict[str, Any]:
    engine_status = control.status()
    payload: dict[str, Any] = {
        "broker": "unknown",
        "broker_class": "-",
        "engine_running": bool(engine_status.get("running")),
        "engine_mode": engine_status.get("mode"),
        "kill_switch_active": bool(engine_status.get("kill_switch_active")),
        "connected": False,
        "account": "",
        "host": "",
        "port": "",
        "server_time": None,
        "error": "",
        "message": "",
        "capabilities": {},
        "ready": False,
    }

    engine = getattr(control, "engine", None)
    bot = getattr(engine, "_bot", None) if engine else None
    broker = getattr(bot, "broker", None) if bot else None

    # Fall back to shared broker session if engine broker not available
    if not broker and _broker is not None and _broker.is_connected():
        broker = _broker

    if not broker:
        payload["message"] = "No active broker session. POST /api/broker/connect to connect."
        return payload

    broker_class = broker.__class__.__name__
    payload["broker_class"] = broker_class
    payload["broker"] = broker_class.replace("Broker", "").lower() or "unknown"

    try:
        caps = broker.get_capabilities() if hasattr(broker, "get_capabilities") else {}
        if is_dataclass(caps):
            payload["capabilities"] = asdict(caps)
        elif isinstance(caps, dict):
            payload["capabilities"] = dict(caps)
    except Exception:
        payload["capabilities"] = {}

    health_data: dict[str, Any] = {}
    if hasattr(broker, "health_check"):
        try:
            health_data = broker.health_check() or {}
        except Exception as exc:
            payload["error"] = str(exc)
            health_data = {}

    if health_data:
        payload["connected"] = bool(health_data.get("connected", False))
        payload["account"] = str(health_data.get("account") or "")
        payload["host"] = str(health_data.get("host") or "")
        payload["port"] = str(health_data.get("port") or "")
        payload["server_time"] = health_data.get("server_time")
        if health_data.get("error"):
            payload["error"] = str(health_data.get("error"))
    else:
        if hasattr(broker, "is_connected"):
            try:
                payload["connected"] = bool(broker.is_connected())
            except Exception as exc:
                payload["error"] = str(exc)
        else:
            payload["connected"] = bool(engine_status.get("running"))

    payload["ready"] = bool(
        payload["connected"]
        and not payload["kill_switch_active"]
        and not payload["error"]
    )
    if not payload["message"]:
        if payload["ready"] and payload["engine_running"]:
            payload["message"] = "Broker lane ready."
        elif payload["ready"]:
            payload["message"] = "Broker connected (engine not running)."
        else:
            payload["message"] = "Broker lane degraded."

    return payload


def _get_editable_settings() -> dict[str, Any]:
    overrides = config._load_runtime_overrides()
    return {
        "broker": {
            "broker_mode": overrides.get("broker_mode", config.BROKER_MODE),
            "trading_mode": overrides.get("trading_mode", config.TRADING_MODE),
        },
        "risk_limits": {
            "portfolio_initial_capital": overrides.get("portfolio_initial_capital", config.PORTFOLIO["initial_capital"]),
            "portfolio_default_stake": overrides.get("portfolio_default_stake", config.PORTFOLIO["default_stake_per_point"]),
            "portfolio_max_positions": overrides.get("portfolio_max_positions", config.PORTFOLIO["max_open_positions"]),
            "portfolio_max_exposure_pct": overrides.get("portfolio_max_exposure_pct", config.PORTFOLIO["max_exposure_pct"]),
        },
        "ibs_parameters": {
            "ibs_entry_thresh": overrides.get("ibs_entry_thresh", config.IBS_PARAMS["ibs_entry_thresh"]),
            "ibs_exit_thresh": overrides.get("ibs_exit_thresh", config.IBS_PARAMS["ibs_exit_thresh"]),
            "ibs_use_rsi_filter": overrides.get("ibs_use_rsi_filter", config.IBS_PARAMS["use_rsi_filter"]),
            "ibs_rsi_period": overrides.get("ibs_rsi_period", config.IBS_PARAMS["rsi_period"]),
            "ibs_rsi_entry_thresh": overrides.get("ibs_rsi_entry_thresh", config.IBS_PARAMS["rsi_entry_thresh"]),
            "ibs_rsi_exit_thresh": overrides.get("ibs_rsi_exit_thresh", config.IBS_PARAMS["rsi_exit_thresh"]),
            "ibs_ema_period": overrides.get("ibs_ema_period", config.IBS_PARAMS["ema_period"]),
        },
        "notifications": {
            "notifications_enabled": overrides.get("notifications_enabled", config.NOTIFICATIONS["enabled"]),
            "notifications_email_to": overrides.get("notifications_email_to", config.NOTIFICATIONS["email_to"]),
            "notifications_telegram_chat_id": overrides.get("notifications_telegram_chat_id", config.NOTIFICATIONS["telegram_chat_id"]),
        },
    }


def _validate_settings(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "broker_mode" in data and data["broker_mode"] not in ("paper", "demo", "live"):
        errors.append("broker_mode must be paper, demo, or live.")
    if "trading_mode" in data and data["trading_mode"] not in ("shadow", "live"):
        errors.append("trading_mode must be shadow or live.")
    float_fields = {
        "portfolio_initial_capital": (100, 10_000_000),
        "portfolio_default_stake": (0.01, 1000),
        "portfolio_max_exposure_pct": (1, 100),
        "ibs_entry_thresh": (0.01, 0.99),
        "ibs_exit_thresh": (0.01, 0.99),
        "ibs_rsi_entry_thresh": (1, 99),
        "ibs_rsi_exit_thresh": (1, 99),
    }
    for field, (lo, hi) in float_fields.items():
        if field in data:
            try:
                val = float(data[field])
                if val < lo or val > hi:
                    errors.append(f"{field} must be between {lo} and {hi}.")
            except (ValueError, TypeError):
                errors.append(f"{field} must be a number.")
    int_fields = {
        "portfolio_max_positions": (1, 100),
        "ibs_rsi_period": (1, 50),
        "ibs_ema_period": (10, 500),
    }
    for field, (lo, hi) in int_fields.items():
        if field in data:
            try:
                val = int(data[field])
                if val < lo or val > hi:
                    errors.append(f"{field} must be between {lo} and {hi}.")
            except (ValueError, TypeError):
                errors.append(f"{field} must be an integer.")
    return errors


def _save_settings_overrides(data: dict[str, Any]) -> None:
    existing = config._load_runtime_overrides()
    type_casts = {
        "portfolio_initial_capital": float,
        "portfolio_default_stake": float,
        "portfolio_max_positions": int,
        "portfolio_max_exposure_pct": float,
        "ibs_entry_thresh": float,
        "ibs_exit_thresh": float,
        "ibs_use_rsi_filter": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
        "ibs_rsi_period": int,
        "ibs_rsi_entry_thresh": float,
        "ibs_rsi_exit_thresh": float,
        "ibs_ema_period": int,
        "notifications_enabled": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
    }
    for key, value in data.items():
        if key in type_casts:
            existing[key] = type_casts[key](value)
        else:
            existing[key] = value
    config._RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    config._SETTINGS_OVERRIDE_PATH.write_text(json.dumps(existing, indent=2))


app = create_app()
