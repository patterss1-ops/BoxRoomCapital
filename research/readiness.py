"""Operational readiness summary for the research system."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable

import config
from data.pg_connection import get_pg_connection, release_pg_connection, research_db_status
from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactType, Engine
from research.market_data.bootstrap import market_data_readiness
from research.shared.sql import fetchall_dicts


def load_pipeline_stage_counts(
    *,
    connection_factory: Callable[[], Any] = get_pg_connection,
    release_factory: Callable[[Any], None] = release_pg_connection,
) -> dict[str, int]:
    """Return current pipeline stage counts keyed by stage name."""
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_stage, COUNT(*) AS total
                FROM research.pipeline_state
                GROUP BY current_stage
                """
            )
            rows = fetchall_dicts(cur)
    finally:
        release_factory(conn)
    return {str(row["current_stage"]): int(row["total"]) for row in rows}


def load_engine_a_tradeability_diagnostics(
    *,
    artifact_store_factory: Callable[[], ArtifactStore] = ArtifactStore,
) -> dict[str, Any]:
    """Inspect the latest Engine A signal/rebalance pair for tradability blockers."""
    store = artifact_store_factory()
    latest_signal = next(
        iter(
            store.query(
                artifact_type=ArtifactType.ENGINE_A_SIGNAL_SET,
                engine=Engine.ENGINE_A,
                limit=1,
            )
        ),
        None,
    )
    latest_rebalance = next(
        iter(
            store.query(
                artifact_type=ArtifactType.REBALANCE_SHEET,
                engine=Engine.ENGINE_A,
                limit=1,
            )
        ),
        None,
    )
    combined_forecast = dict((latest_signal.body if latest_signal else {}) or {}).get("combined_forecast") or {}
    deltas = dict((latest_rebalance.body if latest_rebalance else {}) or {}).get("deltas") or {}
    max_abs_forecast = max((abs(float(value or 0.0)) for value in combined_forecast.values()), default=0.0)
    nonzero_delta_count = sum(1 for value in deltas.values() if abs(float(value or 0.0)) > 0.0)
    return {
        "signal_as_of": str((latest_signal.body if latest_signal else {}).get("as_of") or ""),
        "rebalance_as_of": str((latest_rebalance.body if latest_rebalance else {}).get("as_of") or ""),
        "max_abs_forecast": round(max_abs_forecast, 6),
        "nonzero_delta_count": int(nonzero_delta_count),
    }


def build_research_readiness_report(
    *,
    as_of: date | None = None,
    pipeline_status: dict[str, Any] | None = None,
    db_status: dict[str, Any] | None = None,
    db_status_loader: Callable[[], dict[str, Any]] = research_db_status,
    market_data_loader: Callable[..., dict[str, Any]] = market_data_readiness,
    stage_counts_loader: Callable[[], dict[str, int]] = load_pipeline_stage_counts,
    engine_a_diag_loader: Callable[[], dict[str, Any]] = load_engine_a_tradeability_diagnostics,
) -> dict[str, Any]:
    """Build a compact report for operational activation and validation readiness."""
    report_date = as_of or date.today()
    pipeline = dict(pipeline_status or {})
    db = dict(db_status or db_status_loader())

    stage_counts: dict[str, int] = {}
    stage_count_error = ""
    engine_a_diag: dict[str, Any] = {}
    engine_a_diag_error = ""
    if bool(db.get("schema_ready")):
        try:
            stage_counts = dict(stage_counts_loader() or {})
        except Exception as exc:  # pragma: no cover - exercised by callers
            stage_count_error = str(exc)
        try:
            engine_a_diag = dict(engine_a_diag_loader() or {})
        except Exception as exc:  # pragma: no cover - exercised by callers
            engine_a_diag_error = str(exc)

    review_pending = int(stage_counts.get("review_pending", 0))
    pilot_pending = int(stage_counts.get("pilot_ready", 0))

    market_detail = _build_market_data_check(
        report_date=report_date,
        db=db,
        market_data_loader=market_data_loader,
    )
    engine_a = _build_engine_check(
        "Engine A",
        dict(pipeline.get("engine_a") or {}),
        tradeability_diag=engine_a_diag,
        diag_error=engine_a_diag_error,
    )
    engine_b = _build_engine_check("Engine B", dict(pipeline.get("engine_b") or {}))
    operator_queue = _build_operator_queue_check(
        review_pending=review_pending,
        pilot_pending=pilot_pending,
        db_ready=bool(db.get("schema_ready")),
        stage_count_error=stage_count_error,
    )

    checks = [
        _build_db_check(db),
        market_detail,
        engine_a,
        engine_b,
        operator_queue,
    ]
    issues = _build_next_steps(checks=checks, routing_active=bool(getattr(config, "RESEARCH_SYSTEM_ACTIVE", False)))
    overall_status = _overall_status(checks)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "as_of": report_date.isoformat(),
        "generated_at": generated_at,
        "overall_status": overall_status,
        "routing_mode": "research_primary" if bool(getattr(config, "RESEARCH_SYSTEM_ACTIVE", False)) else "mirror",
        "checks": checks,
        "issues": issues,
        "stage_counts": stage_counts,
        "review_pending_count": review_pending,
        "pilot_signoff_pending_count": pilot_pending,
    }


def _build_db_check(db: dict[str, Any]) -> dict[str, Any]:
    status = "ready" if bool(db.get("schema_ready")) else "blocked"
    return {
        "key": "research_db",
        "label": "Research DB",
        "status": status,
        "headline": str(db.get("status") or "unknown"),
        "detail": str(db.get("detail") or "Research PostgreSQL status unavailable"),
    }


def _build_market_data_check(
    *,
    report_date: date,
    db: dict[str, Any],
    market_data_loader: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if not bool(db.get("schema_ready")):
        return {
            "key": "market_data",
            "label": "Market Data",
            "status": "blocked",
            "headline": "db blocked",
            "detail": "Research DB must be ready before market-data seeding and readiness checks can run.",
            "instrument_count": 0,
            "ready_count": 0,
            "lagging_symbols": [],
        }

    try:
        payload = dict(market_data_loader(as_of=report_date) or {})
    except Exception as exc:
        return {
            "key": "market_data",
            "label": "Market Data",
            "status": "attention",
            "headline": "error",
            "detail": f"Market-data readiness failed: {exc}",
            "instrument_count": 0,
            "ready_count": 0,
            "lagging_symbols": [],
        }

    rows = list(payload.get("rows") or [])
    instrument_count = int(payload.get("instrument_count") or len(rows))
    ready_count = int(payload.get("ready_count") or 0)
    lagging = [str(row.get("symbol") or "").strip() for row in rows if row.get("status") != "ready"]
    latest_dates = [str(row.get("latest_raw_bar") or "") for row in rows if row.get("latest_raw_bar")]

    if instrument_count <= 0:
        status = "attention"
        headline = "unseeded"
        detail = "No seeded research instruments were found."
    elif ready_count >= instrument_count:
        status = "ready"
        headline = f"{ready_count}/{instrument_count} ready"
        detail = f"All seeded instruments have raw plus canonical history as of {max(latest_dates) if latest_dates else report_date.isoformat()}."
    else:
        status = "attention"
        headline = f"{ready_count}/{instrument_count} ready"
        detail = (
            f"{instrument_count - ready_count} instruments are still missing complete raw/canonical history."
        )

    return {
        "key": "market_data",
        "label": "Market Data",
        "status": status,
        "headline": headline,
        "detail": detail,
        "instrument_count": instrument_count,
        "ready_count": ready_count,
        "lagging_symbols": [symbol for symbol in lagging if symbol][:5],
    }


def _build_engine_check(
    label: str,
    payload: dict[str, Any],
    *,
    tradeability_diag: dict[str, Any] | None = None,
    diag_error: str = "",
) -> dict[str, Any]:
    enabled = bool(payload.get("enabled", True))
    configured = bool(payload.get("configured", False))
    last_result = dict(payload.get("last_result") or {})

    if not configured:
        status = "blocked"
        headline = "unconfigured"
        detail = f"{label} factory is not configured."
    elif not last_result:
        status = "pending"
        headline = "disabled" if not enabled else "not_run"
        detail = (
            f"{label} service is disabled in config and no manual validation run has been recorded yet."
            if not enabled
            else f"No recorded {label} validation run yet."
        )
    else:
        raw_status = str(last_result.get("status") or "unknown").lower()
        if raw_status == "ok":
            status = "ready"
        elif raw_status in {"running", "queued", "started"}:
            status = "pending"
        else:
            status = "attention"
        detail_parts = [str(last_result.get("current_stage") or ""), str(last_result.get("error") or "")]
        detail = " ".join(part for part in detail_parts if part).strip() or f"Last run status: {raw_status}"
        headline = raw_status
        if not enabled:
            detail = f"{detail} Service is disabled in config."
        if label == "Engine A" and raw_status == "ok":
            if diag_error:
                status = "attention"
                headline = "diag_error"
                detail = f"{detail} Engine A tradability diagnostic failed: {diag_error}"
            else:
                diag = dict(tradeability_diag or {})
                if (
                    float(diag.get("max_abs_forecast") or 0.0) >= 0.10
                    and int(diag.get("nonzero_delta_count") or 0) == 0
                ):
                    status = "attention"
                    headline = "granularity_blocked"
                    detail = (
                        "Latest Engine A rebalance has zero executable deltas "
                        f"despite max combined forecast {float(diag['max_abs_forecast']):.2f}; "
                        "capital base or contract granularity is too small for the current universe."
                    )

    return {
        "key": label.lower().replace(" ", "_"),
        "label": label,
        "status": status,
        "headline": headline,
        "detail": detail,
        "as_of": str(last_result.get("as_of") or ""),
    }


def _build_operator_queue_check(
    *,
    review_pending: int,
    pilot_pending: int,
    db_ready: bool,
    stage_count_error: str,
) -> dict[str, Any]:
    if not db_ready:
        return {
            "key": "operator_queue",
            "label": "Operator Queue",
            "status": "blocked",
            "headline": "db blocked",
            "detail": "Pipeline-state counts are unavailable until the research DB is ready.",
        }
    if stage_count_error:
        return {
            "key": "operator_queue",
            "label": "Operator Queue",
            "status": "attention",
            "headline": "count_error",
            "detail": stage_count_error,
        }
    if review_pending or pilot_pending:
        return {
            "key": "operator_queue",
            "label": "Operator Queue",
            "status": "pending",
            "headline": f"reviews={review_pending} pilots={pilot_pending}",
            "detail": "Resolve decay reviews and pending pilot sign-offs before calling the system operationally clean.",
        }
    return {
        "key": "operator_queue",
        "label": "Operator Queue",
        "status": "ready",
        "headline": "clear",
        "detail": "No pending decay reviews or pilot sign-offs.",
    }


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "") for check in checks}
    if "blocked" in statuses:
        return "blocked"
    if statuses - {"ready"}:
        return "attention"
    return "ready"


def _build_next_steps(*, checks: list[dict[str, Any]], routing_active: bool) -> list[str]:
    by_key = {str(check.get("key") or ""): check for check in checks}
    steps: list[str] = []
    checks_ready = all(str(check.get("status") or "") == "ready" for check in checks)

    if by_key.get("research_db", {}).get("status") != "ready":
        steps.append("Provision PostgreSQL, set RESEARCH_DB_DSN, and run init_research_schema().")
    if by_key.get("market_data", {}).get("status") != "ready":
        steps.append("Run scripts/bootstrap_research_market_data.py to seed the MVP universe and ingest history.")
    if by_key.get("engine_a", {}).get("status") != "ready":
        if by_key.get("engine_a", {}).get("headline") == "granularity_blocked":
            steps.append("Increase ENGINE_A_CAPITAL_BASE or shrink Engine A contract granularity/universe until the latest non-trivial forecasts produce executable deltas.")
        else:
            steps.append("Run Engine A against the seeded dataset and confirm a DB-backed rebalance chain is produced.")
    if by_key.get("engine_b", {}).get("status") != "ready":
        steps.append("Submit a real/manual Engine B event and verify stage-aware artifacts plus experiment output.")
    if by_key.get("operator_queue", {}).get("status") != "ready":
        steps.append("Resolve pending decay reviews and pilot sign-offs before cutover.")
    if not routing_active:
        if checks_ready:
            steps.append("Research readiness is green; enable RESEARCH_SYSTEM_ACTIVE when you want to leave mirror mode.")
        else:
            steps.append("Leave RESEARCH_SYSTEM_ACTIVE=false until the readiness checks above are green.")
    return steps
