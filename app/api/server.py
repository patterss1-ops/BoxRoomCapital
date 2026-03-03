"""FastAPI app for bot control and monitoring."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from app.api.ledger import router as ledger_router
from app.engine.control import BotControlService
from app.engine.signal_shadow import get_signal_shadow_report, run_signal_shadow_cycle
from app.research.service import ResearchService
from fund.promotion_gate import build_promotion_gate_report, validate_lane_transition
from fund.nav import calculate_fund_nav
from intelligence.jobs.signal_layer_jobs import (
    enrich_signal_shadow_payload,
    run_tier1_shadow_jobs,
)
from data.trade_db import (
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
    init_db,
    insert_calibration_points,
    log_event,
    promote_strategy_parameter_set,
    update_job,
)
from intelligence.webhook_server import (
    WebhookValidationError,
    build_audit_detail,
    extract_auth_token,
    parse_json_payload,
    summarize_payload,
    validate_expected_token,
)
from fund.execution_quality import get_execution_quality_payload
from app.metrics import build_api_health_payload, build_prometheus_metrics_payload
from risk.portfolio_risk import get_risk_briefing

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "web" / "templates"))
control = BotControlService(PROJECT_ROOT)
research = ResearchService(PROJECT_ROOT)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Trading Bot Control Plane",
        version="1.0.0",
        lifespan=app_lifespan,
    )
    app.include_router(ledger_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(PROJECT_ROOT / "app" / "web" / "static")),
        name="static",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return build_api_health_payload()

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

    @app.get("/api/incidents")
    def api_incidents(limit: int = 50):
        return {"items": get_incidents(limit=limit)}

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

    @app.post("/api/webhooks/tradingview")
    async def tradingview_webhook(request: Request, token: str = ""):
        payload: Optional[dict[str, Any]] = None
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
            signal = summarize_payload(payload)
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

        _safe_log_event(
            category="SIGNAL",
            headline=f"TradingView webhook accepted: {signal.action or '-'} {signal.ticker or '-'}",
            detail=build_audit_detail(reason="accepted", client_ip=client_ip, payload=payload),
            ticker=signal.ticker or None,
            strategy=signal.strategy or "tradingview_webhook",
        )
        return {
            "ok": True,
            "message": "TradingView webhook accepted.",
            "ticker": signal.ticker,
            "action": signal.action,
            "strategy": signal.strategy,
            "timeframe": signal.timeframe,
        }

    @app.post("/api/actions/start", response_class=HTMLResponse)
    def start_bot(mode: str = Form(default=config.TRADING_MODE)):
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="start_bot", status="running", mode=mode)
        result = control.start(mode=mode)
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/stop", response_class=HTMLResponse)
    def stop_bot():
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="stop_bot", status="running")
        result = control.stop()
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/pause", response_class=HTMLResponse)
    def pause_bot():
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="pause_bot", status="running")
        result = control.pause()
        if result["ok"]:
            update_job(job_id, status="completed", result=result["message"])
            return action_message(result["message"], ok=True)
        update_job(job_id, status="failed", error=result["message"])
        return action_message(result["message"], ok=False)

    @app.post("/api/actions/resume", response_class=HTMLResponse)
    def resume_bot():
        job_id = str(uuid.uuid4())
        create_job(job_id=job_id, job_type="resume_bot", status="running")
        result = control.resume()
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
        return TEMPLATES.TemplateResponse(
            request,
            "trading.html",
            _page_context(request=request, page_key="trading", title="Trading | Trading Bot"),
        )

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

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "settings_page.html",
            _page_context(request=request, page_key="settings", title="Settings | Trading Bot"),
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
                "incidents": get_incidents(limit=25),
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
        latest = get_incidents(limit=1)
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
            {"request": request, "incidents": get_incidents(limit=25)},
        )

    @app.get("/fragments/control-actions", response_class=HTMLResponse)
    def control_actions_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "_control_actions.html",
            {"request": request, "control_actions": get_control_actions(limit=25)},
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

    return app


def action_message(text: str, ok: bool) -> str:
    css = "action-msg ok" if ok else "action-msg error"
    return f"<div class='{css}'>{text}</div>"


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
    result = control.scan_once(mode=mode)
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
    result = control.reconcile()
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

    if not broker:
        payload["message"] = "No active broker session (engine not running)."
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
        payload["engine_running"]
        and payload["connected"]
        and not payload["kill_switch_active"]
        and not payload["error"]
    )
    if not payload["message"]:
        payload["message"] = "Broker lane ready." if payload["ready"] else "Broker lane degraded."

    return payload


app = create_app()
