"""Shared operator surface helpers for incidents, status, and analytics."""
from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Any, Callable, Optional


def _incident_detail_payload(item: Optional[dict[str, Any]], *, json_loads: Callable[[str], Any]) -> Optional[dict[str, Any]]:
    if not item:
        return None
    detail = item.get("detail")
    if not isinstance(detail, str):
        return None
    try:
        payload = json_loads(detail)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _is_test_artifact_incident(
    item: Optional[dict[str, Any]],
    *,
    incident_detail_payload: Callable[[Optional[dict[str, Any]]], Optional[dict[str, Any]]],
) -> bool:
    payload = incident_detail_payload(item)
    if payload is None:
        return False
    return payload.get("client_ip") == "testclient"


def _is_loopback_client_ip(value: Any) -> bool:
    clean = str(value or "").strip().lower()
    if not clean:
        return False
    if clean == "localhost":
        return True
    if clean.startswith("[") and "]" in clean:
        clean = clean[1:clean.index("]")]
    elif clean.count(":") == 1:
        host, port = clean.rsplit(":", 1)
        if port.isdigit():
            clean = host
    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def _is_localhost_tradingview_rejection_incident(
    item: Optional[dict[str, Any]],
    *,
    incident_detail_payload: Callable[[Optional[dict[str, Any]]], Optional[dict[str, Any]]],
    is_loopback_client_ip: Callable[[Any], bool],
) -> bool:
    if not item or item.get("title") != "TradingView webhook rejected":
        return False
    payload = incident_detail_payload(item)
    if payload is None:
        return False
    return is_loopback_client_ip(payload.get("client_ip"))


def _normalize_incident_mode(mode: str) -> str:
    clean = str(mode or "").strip().lower()
    return clean if clean in {"active", "history"} else "active"


def _incident_timestamp(item: Optional[dict[str, Any]]) -> Optional[datetime]:
    if not item:
        return None
    raw = str(item.get("timestamp") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_active_incident(
    item: Optional[dict[str, Any]],
    *,
    incident_timestamp: Callable[[Optional[dict[str, Any]]], Optional[datetime]],
    active_incident_event_lookback,
    now: Optional[datetime] = None,
) -> bool:
    if not item:
        return False
    source = str(item.get("source") or "")
    if source == "order_action":
        return True
    if source != "bot_event":
        return False
    timestamp = incident_timestamp(item)
    if timestamp is None:
        return False
    current = now or datetime.now(timezone.utc)
    return timestamp >= current - active_incident_event_lookback


def _visible_incidents(
    *,
    get_incidents: Callable[..., list[dict[str, Any]]],
    is_test_artifact_incident: Callable[[Optional[dict[str, Any]]], bool],
    is_localhost_tradingview_rejection_incident: Callable[[Optional[dict[str, Any]]], bool],
    is_active_incident: Callable[[Optional[dict[str, Any]]], bool],
    limit: int = 25,
    mode: str = "history",
) -> list[dict[str, Any]]:
    incident_mode = _normalize_incident_mode(mode)
    raw_incidents = get_incidents(limit=max(limit * 4, limit))
    visible: list[dict[str, Any]] = []
    for incident in raw_incidents:
        if is_test_artifact_incident(incident) or is_localhost_tradingview_rejection_incident(incident):
            continue
        if incident_mode == "active" and not is_active_incident(incident):
            continue
        visible.append(incident)
        if len(visible) >= limit:
            break
    return visible


def _safe_log_event(*, log_event: Callable[..., Any], **kwargs: Any) -> None:
    try:
        log_event(**kwargs)
    except Exception:
        return


def build_status_payload(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    status_cache_ttl_seconds: float,
    control_status: Callable[[], dict[str, Any]],
    get_summary: Callable[[], Any],
    get_open_option_positions: Callable[[], Any],
) -> dict[str, Any]:
    return get_cached_value(
        "status-payload",
        status_cache_ttl_seconds,
        lambda: {
            "engine": control_status(),
            "summary": get_summary(),
            "open_option_positions": get_open_option_positions(),
        },
        stale_on_error=True,
    )


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


def build_risk_briefing_payload(
    *,
    calculate_fund_nav: Callable[[], Any],
    get_risk_briefing: Callable[..., dict[str, Any]],
    unavailable_risk_briefing_payload: Callable[[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    try:
        nav = calculate_fund_nav()
        if nav.total_nav <= 0 and nav.total_cash <= 0 and nav.total_positions_value <= 0:
            return unavailable_risk_briefing_payload(
                "No ledger data available yet.",
                "Sync broker cash/positions and reload.",
                "RISK_DATA_UNAVAILABLE",
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
        return unavailable_risk_briefing_payload(
            "Risk briefing provider failed.",
            "Check risk/nav services and retry.",
            "RISK_DATA_ERROR",
        )


def build_portfolio_analytics_payload(
    days: int,
    *,
    max_days: int,
    rolling_window_default: int,
    risk_free_rate: float,
    get_fund_daily_reports: Callable[..., list[dict[str, Any]]],
    compute_metrics: Callable[..., Any],
    compute_drawdowns: Callable[..., list[Any]],
    compute_rolling_stats: Callable[..., Any],
) -> dict[str, Any]:
    bounded_days = max(7, min(int(days), int(max_days)))
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
            "rolling": {
                "window": rolling_window_default,
                "dates": [],
                "rolling_return_pct": [],
                "rolling_volatility_pct": [],
                "rolling_sharpe": [],
            },
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
        risk_free_rate=float(risk_free_rate),
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
        int(rolling_window_default),
        len(returns) if returns else int(rolling_window_default),
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


def _page_context(
    request: Any,
    page_key: str,
    title: str,
    *,
    build_status_payload: Callable[[], dict[str, Any]],
    trading_mode: str,
    build_research_system_state_context: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    payload = build_status_payload()
    return {
        "request": request,
        "title": title,
        "page_key": page_key,
        "status": payload["engine"],
        "summary": payload["summary"],
        "open_positions": payload["open_option_positions"],
        "default_mode": trading_mode,
        **build_research_system_state_context(),
    }
