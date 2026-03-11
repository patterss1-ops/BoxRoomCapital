"""Shared background-job and job-summary helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def _run_scan_job(
    job_id: str,
    mode: str,
    *,
    update_job: Callable[..., Any],
    scan_once: Callable[..., dict[str, Any]],
) -> None:
    update_job(job_id, status="running", detail=f"Running one-shot scan ({mode.upper()})")
    try:
        result = scan_once(mode=mode)
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


def _run_reconcile_job(
    job_id: str,
    *,
    update_job: Callable[..., Any],
    reconcile: Callable[[], dict[str, Any]],
) -> None:
    update_job(job_id, status="running", detail="Running reconcile")
    try:
        result = reconcile()
    except Exception as exc:
        update_job(job_id, status="failed", detail="Reconcile crashed", error=str(exc))
        return
    if result["ok"]:
        update_job(job_id, status="completed", detail=result["message"])
        return
    update_job(job_id, status="failed", detail=result["message"], error=result.get("message"))


def _run_signal_shadow_job(
    job_id: str,
    *,
    update_job: Callable[..., Any],
    run_signal_shadow_cycle: Callable[[], dict[str, Any]],
) -> None:
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


def _run_signal_tier1_job(
    job_id: str,
    *,
    update_job: Callable[..., Any],
    run_tier1_shadow_jobs: Callable[[], dict[str, Any]],
) -> None:
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


def _run_close_job(
    job_id: str,
    spread_id: str,
    ticker: str,
    reason: str,
    *,
    update_job: Callable[..., Any],
    close_spread: Callable[..., dict[str, Any]],
) -> None:
    update_job(job_id, status="running", detail="Closing spread")
    result = close_spread(spread_id=spread_id, ticker=ticker, reason=reason)
    if result["ok"]:
        update_job(job_id, status="completed", detail=result["message"])
        return
    update_job(job_id, status="failed", detail=result["message"], error=result.get("message"))


def _run_discovery_job(
    job_id: str,
    mode: str,
    details: bool,
    strikes: str,
    *,
    update_job: Callable[..., Any],
    run_discovery: Callable[..., dict[str, Any]],
) -> None:
    update_job(job_id, status="running", detail=f"Running options discovery ({mode})")
    search_only = mode == "search"
    nav_only = mode == "nav"
    result = run_discovery(
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


def _run_calibration_job(
    job_id: str,
    index_filter: str,
    verbose: bool,
    *,
    create_calibration_run: Callable[..., Any],
    update_job: Callable[..., Any],
    run_calibration: Callable[..., dict[str, Any]],
    insert_calibration_points: Callable[..., int],
    complete_calibration_run: Callable[..., Any],
) -> None:
    scope = index_filter or "all"
    create_calibration_run(run_id=job_id, scope=scope, status="running")
    update_job(job_id, status="running", detail=f"Running calibration ({scope})")
    result = run_calibration(index_filter=index_filter, verbose=verbose)
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


def _summarize_top_candidates(rows: Any, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    top: list[dict[str, Any]] = []
    for row in rows[: max(1, int(limit))]:
        if not isinstance(row, dict):
            continue
        research_vetoes = [code for code in (row.get("research_vetoes") or []) if str(code or "").strip()]
        top.append(
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "action": str(row.get("action") or ""),
                "final_score": row.get("final_score"),
                "rank_score": row.get("rank_score"),
                "research_layer_score": row.get("research_layer_score"),
                "research_vetoes": research_vetoes,
            }
        )
    return top


def _build_signal_shadow_job_summary(
    parsed_result: Any,
    *,
    build_ranked_candidates: Callable[..., list[dict[str, Any]]],
    summarize_research_overlay: Callable[[dict[str, Any]], Any],
) -> dict[str, Any] | None:
    if not isinstance(parsed_result, dict):
        return None
    report = parsed_result
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    ranked = build_ranked_candidates(report, limit=3)
    research_overlay = summarize_research_overlay(report)
    return {
        "kind": "signal_shadow_run",
        "title": "Signal Shadow Summary",
        "run_id": str(report.get("run_id") or ""),
        "run_at": str(report.get("run_at") or ""),
        "tickers_total": int(summary.get("tickers_total", 0)),
        "tickers_scored": int(summary.get("tickers_scored", 0)),
        "ranked_count": len(build_ranked_candidates(report, limit=20)),
        "blocked_missing": int(summary.get("tickers_blocked_missing_required_layers", 0)),
        "blocked_stale": int(summary.get("tickers_blocked_stale_layers", 0)),
        "research_overlay": research_overlay,
        "top_candidates": _summarize_top_candidates(ranked, limit=3),
    }


def _build_signal_tier1_job_summary(
    parsed_result: Any,
    *,
    build_ranked_candidates: Callable[..., list[dict[str, Any]]],
    summarize_research_overlay: Callable[[dict[str, Any]], Any],
) -> dict[str, Any] | None:
    if not isinstance(parsed_result, dict):
        return None
    report = parsed_result.get("shadow_report") if isinstance(parsed_result.get("shadow_report"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    ranked = build_ranked_candidates(report, limit=3) if report else []
    research_overlay = summarize_research_overlay(report) if report else summarize_research_overlay({})
    research_summary = parsed_result.get("research_summary") if isinstance(parsed_result.get("research_summary"), dict) else {}
    layer_jobs = parsed_result.get("layer_jobs") if isinstance(parsed_result.get("layer_jobs"), dict) else {}
    l9_job = layer_jobs.get("l9_research") if isinstance(layer_jobs.get("l9_research"), dict) else {}
    return {
        "kind": "signal_tier1_shadow_run",
        "title": "Tier-1 Shadow Summary",
        "run_id": str(parsed_result.get("run_id") or report.get("run_id") or ""),
        "run_at": str(parsed_result.get("run_at") or report.get("run_at") or ""),
        "tickers_total": int(summary.get("tickers_total", 0)),
        "tickers_scored": int(summary.get("tickers_scored", 0)),
        "ranked_count": len(parsed_result.get("ranked_candidates") or []),
        "blocked_missing": int(summary.get("tickers_blocked_missing_required_layers", 0)),
        "blocked_stale": int(summary.get("tickers_blocked_stale_layers", 0)),
        "research_overlay": research_overlay,
        "research_job": {
            "status": str(l9_job.get("status") or ""),
            "detail": str(l9_job.get("detail") or ""),
            "job_id": str(l9_job.get("job_id") or ""),
            "tickers_success": int(research_summary.get("tickers_success", 0)),
            "tickers_failed": int(research_summary.get("tickers_failed", 0)),
            "tickers_skipped": int(research_summary.get("tickers_skipped", 0)),
        },
        "top_candidates": _summarize_top_candidates(ranked, limit=3),
    }


def _build_job_detail_summary(
    job_type: str,
    parsed_result: Any,
    *,
    build_ranked_candidates: Callable[..., list[dict[str, Any]]],
    summarize_research_overlay: Callable[[dict[str, Any]], Any],
) -> dict[str, Any] | None:
    clean_job_type = str(job_type or "").strip().lower()
    if clean_job_type == "signal_shadow_run":
        return _build_signal_shadow_job_summary(
            parsed_result,
            build_ranked_candidates=build_ranked_candidates,
            summarize_research_overlay=summarize_research_overlay,
        )
    if clean_job_type == "signal_tier1_shadow_run":
        return _build_signal_tier1_job_summary(
            parsed_result,
            build_ranked_candidates=build_ranked_candidates,
            summarize_research_overlay=summarize_research_overlay,
        )
    return None
