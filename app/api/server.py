"""FastAPI app for bot control and monitoring."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from app.engine.control import BotControlService
from app.research.service import ResearchService
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
    get_job,
    get_active_strategy_parameter_set,
    get_incidents,
    get_jobs,
    get_open_option_positions,
    get_option_contract_summary,
    get_option_contracts,
    get_order_actions,
    get_risk_verdicts,
    get_risk_verdict_summary,
    get_strategy_parameter_sets,
    get_strategy_promotions,
    get_summary,
    init_db,
    insert_calibration_points,
    promote_strategy_parameter_set,
    update_job,
)
from execution.ledger import (
    get_broker_accounts,
    get_unified_positions,
    get_latest_cash_balances,
    get_nav_history,
    get_reconciliation_reports,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "web" / "templates"))
control = BotControlService(PROJECT_ROOT)
research = ResearchService(PROJECT_ROOT)


def create_app() -> FastAPI:
    app = FastAPI(title="Trading Bot Control Plane", version="1.0.0")
    app.mount(
        "/static",
        StaticFiles(directory=str(PROJECT_ROOT / "app" / "web" / "static")),
        name="static",
    )

    @app.on_event("startup")
    def _startup():
        init_db()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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

    @app.get("/api/log-tail")
    def api_log_tail(lines: int = 200):
        try:
            text = _tail_file(control.process_log, lines=lines)
        except FileNotFoundError:
            text = ""
        return JSONResponse({"log": text})

    # ─── A-007: Phase A — Multi-broker fund surfaces ──────────────────────

    @app.get("/api/broker/accounts")
    def api_broker_accounts(broker: str = "", active_only: bool = True):
        """List registered broker accounts from the multi-broker ledger."""
        return {
            "items": get_broker_accounts(
                broker=broker or None,
                active_only=active_only,
            )
        }

    @app.get("/api/broker/positions")
    def api_broker_positions(broker: str = "", sleeve: str = ""):
        """Unified positions across all brokers."""
        return {
            "items": get_unified_positions(
                broker=broker or None,
                sleeve=sleeve or None,
            )
        }

    @app.get("/api/broker/cash")
    def api_broker_cash():
        """Latest cash balances per broker account."""
        return {"items": get_latest_cash_balances()}

    @app.get("/api/broker/health")
    def api_broker_health():
        """
        Broker connection health status.

        Returns health info for each registered broker. Actual health checks
        would call broker.health_check() in production; for now returns
        registration state and last sync times.
        """
        accounts = get_broker_accounts(active_only=True)
        brokers: dict[str, dict] = {}
        for acct in accounts:
            b = acct["broker"]
            if b not in brokers:
                brokers[b] = {
                    "broker": b,
                    "accounts": 0,
                    "status": "registered",
                    "last_updated": acct.get("updated_at"),
                }
            brokers[b]["accounts"] += 1
            # Track the most recent update across all accounts for this broker
            if acct.get("updated_at") and (
                not brokers[b]["last_updated"]
                or acct["updated_at"] > brokers[b]["last_updated"]
            ):
                brokers[b]["last_updated"] = acct["updated_at"]

        return {"items": list(brokers.values())}

    @app.get("/api/nav/history")
    def api_nav_history(level: str = "fund", level_id: str = "fund", days: int = 30):
        """NAV history for charting (fund, sleeve, or account level)."""
        return {
            "items": get_nav_history(
                level=level,
                level_id=level_id,
                days=days,
            )
        }

    @app.get("/api/risk/verdicts")
    def api_risk_verdicts(
        limit: int = 50, approved: str = "", ticker: str = ""
    ):
        """Recent pre-trade risk gate verdicts."""
        approved_val = None
        if approved == "1":
            approved_val = 1
        elif approved == "0":
            approved_val = 0
        return {
            "items": get_risk_verdicts(
                limit=limit,
                approved=approved_val,
                ticker=ticker or None,
            )
        }

    @app.get("/api/risk/summary")
    def api_risk_summary():
        """Risk verdict summary statistics."""
        return get_risk_verdict_summary()

    @app.get("/api/reconciliation/reports")
    def api_reconciliation_reports(broker_account_id: str = "", limit: int = 10):
        """Multi-broker reconciliation report history."""
        return {
            "items": get_reconciliation_reports(
                broker_account_id=broker_account_id or None,
                limit=limit,
            )
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
        if not clean_set_id:
            return action_message("set_id is required.", ok=False)
        if not clean_ack:
            return action_message("acknowledgement is required.", ok=False)

        job_id = str(uuid.uuid4())
        create_job(
            job_id=job_id,
            job_type="strategy_params_promote",
            status="running",
            detail=f"set={clean_set_id[:8]} -> {target_status}",
        )
        result = promote_strategy_parameter_set(
            set_id=clean_set_id,
            to_status=target_status,
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
            "overview.html",
            _page_context(request=request, page_key="overview", title="Overview | Trading Bot"),
        )

    @app.get("/overview", response_class=HTMLResponse)
    def overview_page_alias(request: Request):
        return TEMPLATES.TemplateResponse(
            "overview.html",
            _page_context(request=request, page_key="overview", title="Overview | Trading Bot"),
        )

    @app.get("/trading", response_class=HTMLResponse)
    def trading_page(request: Request):
        return TEMPLATES.TemplateResponse(
            "trading.html",
            _page_context(request=request, page_key="trading", title="Trading | Trading Bot"),
        )

    @app.get("/research", response_class=HTMLResponse)
    def research_page(request: Request):
        return TEMPLATES.TemplateResponse(
            "research_page.html",
            _page_context(request=request, page_key="research", title="Research | Trading Bot"),
        )

    @app.get("/incidents", response_class=HTMLResponse)
    def incidents_page(request: Request):
        return TEMPLATES.TemplateResponse(
            "incidents_page.html",
            _page_context(request=request, page_key="incidents", title="Incidents & Jobs | Trading Bot"),
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return TEMPLATES.TemplateResponse(
            "settings_page.html",
            _page_context(request=request, page_key="settings", title="Settings | Trading Bot"),
        )

    @app.get("/fund", response_class=HTMLResponse)
    def fund_page(request: Request):
        return TEMPLATES.TemplateResponse(
            "fund_page.html",
            _page_context(request=request, page_key="fund", title="Fund | Trading Bot"),
        )

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy_single_page(request: Request):
        payload = build_status_payload()
        return TEMPLATES.TemplateResponse(
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
            "_events.html",
            {"request": request, "events": get_bot_events(limit=25)},
        )

    @app.get("/fragments/order-actions", response_class=HTMLResponse)
    def order_actions_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            "_order_actions.html",
            {"request": request, "order_actions": get_order_actions(limit=25)},
        )

    @app.get("/fragments/incidents", response_class=HTMLResponse)
    def incidents_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            "_incidents.html",
            {"request": request, "incidents": get_incidents(limit=25)},
        )

    @app.get("/fragments/control-actions", response_class=HTMLResponse)
    def control_actions_fragment(request: Request):
        return TEMPLATES.TemplateResponse(
            "_control_actions.html",
            {"request": request, "control_actions": get_control_actions(limit=25)},
        )

    @app.get("/fragments/reconcile-report", response_class=HTMLResponse)
    def reconcile_report_fragment(request: Request):
        report = control.reconcile_report().get("report", {})
        return TEMPLATES.TemplateResponse(
            "_reconcile_report.html",
            {"request": request, "report": report},
        )

    @app.get("/fragments/research", response_class=HTMLResponse)
    def research_fragment(request: Request):
        calibration_runs = get_calibration_runs(limit=20)
        latest_calibration_run_id = calibration_runs[0]["id"] if calibration_runs else ""
        return TEMPLATES.TemplateResponse(
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
            "_log_tail.html",
            {"request": request, "log_text": log_text},
        )

    # ─── A-007: Fund dashboard fragments ──────────────────────────────────

    @app.get("/fragments/broker-health", response_class=HTMLResponse)
    def broker_health_fragment(request: Request):
        accounts = get_broker_accounts(active_only=True)
        brokers: dict[str, dict] = {}
        for acct in accounts:
            b = acct["broker"]
            if b not in brokers:
                brokers[b] = {
                    "broker": b,
                    "accounts": [],
                    "status": "registered",
                    "last_updated": acct.get("updated_at"),
                }
            brokers[b]["accounts"].append(acct)
            if acct.get("updated_at") and (
                not brokers[b]["last_updated"]
                or acct["updated_at"] > brokers[b]["last_updated"]
            ):
                brokers[b]["last_updated"] = acct["updated_at"]

        return TEMPLATES.TemplateResponse(
            "_broker_health.html",
            {"request": request, "brokers": list(brokers.values())},
        )

    @app.get("/fragments/ledger-positions", response_class=HTMLResponse)
    def ledger_positions_fragment(request: Request, broker: str = "", sleeve: str = ""):
        positions = get_unified_positions(
            broker=broker or None,
            sleeve=sleeve or None,
        )
        return TEMPLATES.TemplateResponse(
            "_ledger_positions.html",
            {"request": request, "positions": positions},
        )

    @app.get("/fragments/ledger-cash", response_class=HTMLResponse)
    def ledger_cash_fragment(request: Request):
        balances = get_latest_cash_balances()
        total = sum(b.get("balance", 0) for b in balances)
        return TEMPLATES.TemplateResponse(
            "_ledger_cash.html",
            {"request": request, "balances": balances, "total_cash": total},
        )

    @app.get("/fragments/risk-verdicts", response_class=HTMLResponse)
    def risk_verdicts_fragment(request: Request, limit: int = 20):
        verdicts = get_risk_verdicts(limit=limit)
        summary = get_risk_verdict_summary()
        return TEMPLATES.TemplateResponse(
            "_risk_verdicts.html",
            {"request": request, "verdicts": verdicts, "summary": summary},
        )

    @app.get("/fragments/nav-history", response_class=HTMLResponse)
    def nav_history_fragment(
        request: Request, level: str = "fund", level_id: str = "fund", days: int = 30
    ):
        history = get_nav_history(level=level, level_id=level_id, days=days)
        return TEMPLATES.TemplateResponse(
            "_nav_history.html",
            {"request": request, "nav_history": history, "level": level, "level_id": level_id},
        )

    @app.get("/fragments/reconciliation-reports", response_class=HTMLResponse)
    def reconciliation_reports_fragment(request: Request, limit: int = 10):
        reports = get_reconciliation_reports(limit=limit)
        return TEMPLATES.TemplateResponse(
            "_reconciliation_reports.html",
            {"request": request, "reports": reports},
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


def build_status_payload() -> dict[str, Any]:
    return {
        "engine": control.status(),
        "summary": get_summary(),
        "open_option_positions": get_open_option_positions(),
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


app = create_app()
