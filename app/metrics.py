"""Prometheus-style metrics and health payload builders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from data.trade_db import DB_PATH, get_conn
from fund.execution_quality import get_execution_quality_payload


def build_api_health_payload(db_path: str = DB_PATH) -> dict[str, Any]:
    """Build API health payload with dependency checks."""
    generated_at = datetime.now(timezone.utc).isoformat()
    checks: dict[str, dict[str, str]] = {}
    status = "ok"

    # DB connectivity check
    try:
        conn = get_conn(db_path)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        checks["db"] = {"status": "ok", "detail": "sqlite connection healthy"}
    except Exception as exc:
        status = "degraded"
        checks["db"] = {"status": "degraded", "detail": str(exc)}

    # Execution telemetry check
    try:
        payload = get_execution_quality_payload(days=14, db_path=db_path)
        attempts = int(payload.get("fills", {}).get("total_attempts", 0) or 0)
        checks["execution_quality"] = {
            "status": "ok",
            "detail": f"window={payload.get('window_label', '14d')}, attempts={attempts}",
        }
    except Exception as exc:
        status = "degraded"
        checks["execution_quality"] = {"status": "degraded", "detail": str(exc)}

    return {
        "status": status,
        "generated_at": generated_at,
        "checks": checks,
    }


def build_metrics_payload(
    days: int = 14,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """Build normalized numeric payload for metrics export."""
    quality = get_execution_quality_payload(days=max(1, int(days)), db_path=db_path)
    fills = quality.get("fills", {})
    latency = quality.get("latency", {})
    slippage = quality.get("slippage", {})

    signal_events_24h = _count_signal_events_last_24h(db_path=db_path)
    ai_gate_reject_24h = _count_ai_gate_rejections_last_24h(db_path=db_path)

    return {
        "window_label": str(quality.get("window_label", f"{days}d")),
        "signal_scoring_total_24h": float(signal_events_24h),
        "ai_gate_rejections_total_24h": float(ai_gate_reject_24h),
        "execution_fill_rate_pct": _to_float(fills.get("fill_rate_pct")),
        "execution_reject_rate_pct": _to_float(fills.get("reject_rate_pct")),
        "execution_mean_latency_ms": _to_float(latency.get("mean_ms")),
        "execution_mean_slippage_bps": _to_float(slippage.get("mean_bps")),
    }


def build_prometheus_metrics_payload(
    days: int = 14,
    db_path: str = DB_PATH,
) -> str:
    """Render Prometheus text exposition format payload."""
    payload = build_metrics_payload(days=days, db_path=db_path)
    return render_prometheus_metrics(payload)


def render_prometheus_metrics(payload: dict[str, Any]) -> str:
    """Render core metrics in Prometheus exposition format."""
    window = str(payload.get("window_label", "14d"))
    lines = [
        "# HELP brc_signal_scoring_total_24h Signal scoring events in last 24h.",
        "# TYPE brc_signal_scoring_total_24h gauge",
        f"brc_signal_scoring_total_24h {payload.get('signal_scoring_total_24h', 0.0)}",
        "# HELP brc_ai_gate_rejections_total_24h AI gate rejections in last 24h.",
        "# TYPE brc_ai_gate_rejections_total_24h gauge",
        f"brc_ai_gate_rejections_total_24h {payload.get('ai_gate_rejections_total_24h', 0.0)}",
        "# HELP brc_execution_fill_rate_pct Execution fill rate percentage.",
        "# TYPE brc_execution_fill_rate_pct gauge",
        (
            "brc_execution_fill_rate_pct"
            f"{{window=\"{window}\"}} {payload.get('execution_fill_rate_pct', 0.0)}"
        ),
        "# HELP brc_execution_reject_rate_pct Execution reject rate percentage.",
        "# TYPE brc_execution_reject_rate_pct gauge",
        (
            "brc_execution_reject_rate_pct"
            f"{{window=\"{window}\"}} {payload.get('execution_reject_rate_pct', 0.0)}"
        ),
        "# HELP brc_execution_mean_latency_ms Mean dispatch latency in milliseconds.",
        "# TYPE brc_execution_mean_latency_ms gauge",
        (
            "brc_execution_mean_latency_ms"
            f"{{window=\"{window}\"}} {payload.get('execution_mean_latency_ms', 0.0)}"
        ),
        "# HELP brc_execution_mean_slippage_bps Mean execution slippage in bps.",
        "# TYPE brc_execution_mean_slippage_bps gauge",
        (
            "brc_execution_mean_slippage_bps"
            f"{{window=\"{window}\"}} {payload.get('execution_mean_slippage_bps', 0.0)}"
        ),
    ]
    return "\n".join(lines) + "\n"


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _count_signal_events_last_24h(db_path: str) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS c
               FROM bot_events
               WHERE category = 'SIGNAL'
                 AND timestamp >= ?""",
            (cutoff,),
        ).fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


def _count_ai_gate_rejections_last_24h(db_path: str) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS c
               FROM order_intent_transitions
               WHERE transition_at >= ?
                 AND (
                   LOWER(COALESCE(error_code, '')) LIKE 'ai_%'
                   OR LOWER(COALESCE(error_message, '')) LIKE '%ai gate%'
                 )""",
            (cutoff,),
        ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        # order_intent tables may be absent in early environments
        return 0
    finally:
        conn.close()
