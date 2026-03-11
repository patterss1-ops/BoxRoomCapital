"""System health, status, metrics, and control-plane utility routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import config
from app.api.shared import control
from app.api.routes.fragments import _build_sa_symbol_capture_cards

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/health")
def api_health() -> dict[str, Any]:
    return build_api_health_payload()


@router.get("/api/preflight")
def api_preflight(request: Request) -> dict[str, Any]:
    """Return preflight check results and pipeline status."""
    preflight = getattr(request.app.state, "preflight", {})
    return {
        "services": preflight,
        "pipeline": control.pipeline_status(),
        "config_warnings": config.validate_critical_config(),
    }


@router.get("/api/metrics")
def api_metrics(days: int = 14):
    payload = build_prometheus_metrics_payload(days=days)
    return Response(
        content=payload,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/api/status")
def api_status() -> dict[str, Any]:
    return build_status_payload()


@router.get("/api/events")
def api_events(limit: int = 50):
    return {"items": get_bot_events(limit=limit)}


@router.get("/api/jobs")
def api_jobs(limit: int = 50):
    _expire_stale_intel_analysis_jobs()
    return {"items": get_jobs(limit=limit)}


@router.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    item = get_job(job_id)
    if not item:
        return JSONResponse({"error": "job_not_found"}, status_code=404)
    return {"item": item}


@router.get("/api/incidents")
def api_incidents(limit: int = 50, mode: str = "history"):
    return {"items": _visible_incidents(limit=limit, mode=mode)}


@router.get("/api/control-actions")
def api_control_actions(limit: int = 50):
    return {"items": get_control_actions(limit=limit)}


@router.get("/api/log-tail")
def api_log_tail(lines: int = 200):
    try:
        text = _tail_file(control.process_log, lines=lines)
    except FileNotFoundError:
        text = ""
    return JSONResponse({"log": text})


@router.get("/api/sa/snapshots")
def api_sa_symbol_snapshots(limit: int = 5):
    cards = _build_sa_symbol_capture_cards(limit=min(max(limit, 1), 20))
    return {"ok": True, "count": len(cards), "items": cards}
