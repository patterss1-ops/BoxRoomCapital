"""Shared council and Engine B intake helpers."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def _expire_stale_intel_analysis_jobs(
    *,
    get_conn: Callable[[str], Any],
    db_path: str,
    parse_iso_datetime: Callable[[Any], datetime | None],
    update_job: Callable[..., Any],
    stale_seconds: float,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=stale_seconds)
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT id, created_at, updated_at
           FROM jobs
           WHERE job_type = 'intel_analysis' AND status IN ('queued', 'running')"""
    ).fetchall()
    conn.close()

    stale_ids: list[str] = []
    for row in rows:
        heartbeat = parse_iso_datetime(row["updated_at"] or row["created_at"])
        if heartbeat is not None and heartbeat < cutoff:
            stale_ids.append(str(row["id"]))

    for job_id in stale_ids:
        update_job(
            job_id,
            status="failed",
            error="Council analysis became stale; the worker likely exited before completion.",
            db_path=db_path,
        )
    return len(stale_ids)


def _build_engine_b_submission_content(submission: Any) -> str:
    lines: list[str] = []
    if submission.title:
        lines.append(f"Title: {submission.title}")
    if submission.author:
        lines.append(f"Author: {submission.author}")
    if submission.tickers:
        lines.append(f"Tickers: {', '.join(submission.tickers[:10])}")
    if submission.url:
        lines.append(f"URL: {submission.url}")
    metadata = dict(submission.metadata or {})
    for key in (
        "sa_page_type",
        "sa_excerpt",
        "sa_published_at",
        "sa_canonical_url",
    ):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            label = key.replace("sa_", "SA ").replace("_", " ").title()
            lines.append(f"{label}: {value}")
    if lines:
        lines.append("")
    lines.append(submission.content.strip())
    return "\n".join(part for part in lines if part).strip()


def _queue_council_analysis(
    submission: Any,
    *,
    detail: str,
    create_job: Callable[..., Any],
    analyze_intel_async: Callable[[Any, str], Any],
) -> str:
    job_id = str(uuid.uuid4())
    create_job(job_id=job_id, job_type="intel_analysis", status="queued", detail=detail)
    analyze_intel_async(submission, job_id)
    return job_id


def _queue_engine_b_intake(
    *,
    raw_content: str,
    source_class: str,
    source_ids: list[str],
    detail: str,
    score_source: Callable[..., float],
    create_job: Callable[..., Any],
    update_job: Callable[..., Any],
    submit_engine_b_event: Callable[..., dict[str, Any]],
    invalidate_research_cached_values: Callable[[], None],
    job_type: str = "engine_b_intake",
    source_credibility: float | None = None,
    allow_ad_hoc: bool = True,
) -> dict[str, Any]:
    content = raw_content.strip()
    if not content:
        return {"ok": False, "error": "missing_content", "detail": "Raw content is required."}

    normalized_ids = [str(item).strip() for item in source_ids if str(item).strip()]
    if not normalized_ids:
        normalized_ids = [f"{source_class}:{uuid.uuid4().hex[:8]}"]

    try:
        credibility = (
            max(0.0, min(1.0, float(source_credibility)))
            if source_credibility is not None
            else score_source(
                source_class=source_class,
                source_ids=normalized_ids,
            )
        )
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": "invalid_source_class", "detail": str(exc)}

    job_id = str(uuid.uuid4())
    create_job(job_id=job_id, job_type=job_type, status="queued", detail=detail[:500])

    def _on_success(summary: dict[str, Any]) -> None:
        update_job(job_id, status="completed", result=json.dumps(summary))
        invalidate_research_cached_values()

    def _on_error(exc: Exception) -> None:
        update_job(job_id, status="failed", error=str(exc))

    result = submit_engine_b_event(
        job_id=job_id,
        raw_content=content,
        source_class=source_class,
        source_credibility=credibility,
        source_ids=normalized_ids,
        on_success=_on_success,
        on_error=_on_error,
        allow_ad_hoc=allow_ad_hoc,
    )
    if result.get("status") != "queued":
        error_detail = result.get("detail") or result.get("status", "unknown")
        update_job(job_id, status="failed", error=error_detail)
        return {
            "ok": False,
            "job_id": job_id,
            "error": "enqueue_failed",
            "detail": error_detail,
        }

    return {
        "ok": True,
        "job_id": job_id,
        "queue_depth": result.get("queue_depth", 0),
        "source_credibility": credibility,
    }


def _build_sa_quant_engine_b_content(payload: dict[str, Any]) -> str:
    ticker = str(payload.get("ticker") or "").strip().upper()
    grades = payload.get("grades") if isinstance(payload.get("grades"), dict) else {}
    grade_summary = ", ".join(
        f"{key}={value}" for key, value in grades.items() if value not in (None, "", [], {})
    )
    lines = [f"Seeking Alpha quant snapshot for {ticker or 'unknown'}"]
    for label, value in (
        ("Title", payload.get("title")),
        ("URL", payload.get("url")),
        ("Rating", payload.get("rating")),
        ("Quant score", payload.get("quant_score")),
        ("Author rating", payload.get("author_rating")),
        ("Wall St rating", payload.get("wall_st_rating")),
        ("Captured at", payload.get("captured_at")),
    ):
        if value not in (None, "", [], {}):
            lines.append(f"{label}: {value}")
    if grade_summary:
        lines.append(f"Factor grades: {grade_summary}")
    return "\n".join(lines)


def _build_finnhub_engine_b_content(payload: dict[str, Any]) -> str:
    lines = []
    for label, value in (
        ("Event type", payload.get("event_type") or payload.get("type")),
        ("Ticker", payload.get("ticker") or payload.get("symbol")),
        ("Title", payload.get("title") or payload.get("headline")),
        ("URL", payload.get("url")),
        ("Published at", payload.get("published_at") or payload.get("datetime")),
        ("Source", payload.get("source")),
    ):
        if value not in (None, "", [], {}):
            lines.append(f"{label}: {value}")
    body = str(payload.get("content") or payload.get("summary") or payload.get("text") or "").strip()
    if body:
        lines.extend(["", body])
    return "\n".join(lines).strip()


def _finnhub_source_class(payload: dict[str, Any]) -> str:
    raw_type = str(payload.get("event_type") or payload.get("type") or "").strip().lower()
    if "transcript" in raw_type:
        return "transcript"
    if "filing" in raw_type or payload.get("form") or payload.get("filing_type"):
        return "filing"
    if "revision" in raw_type or "estimate" in raw_type:
        return "analyst_revision"
    return "news_wire"
