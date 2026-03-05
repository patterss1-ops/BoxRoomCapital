"""Tier-1 Signal Engine job orchestration and ranking helpers (F-007).

Provides:
1. Layer-job orchestration for shadow refresh runs
2. Ranked candidate derivation from shadow reports
3. Freshness diagnostics summaries for operator surfaces
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid
from typing import Any, Callable, Mapping, Optional, Sequence

import config
from app.engine.signal_shadow import run_signal_shadow_cycle
from app.signal.types import LAYER_ORDER, DecisionAction, LayerId
from data.trade_db import DB_PATH
from intelligence.jobs.sa_quant_job import SAQuantJobRunner


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Tier1ShadowJobsConfig:
    """Configuration for tier-1 shadow job orchestration."""

    required_layers: tuple[LayerId, ...] = LAYER_ORDER
    min_layers_for_score: int = 2
    enforce_required_layers: bool = True
    ranking_limit: int = 20


def _extract_strategy_tickers() -> list[str]:
    tickers: set[str] = set()
    for slot in getattr(config, "STRATEGY_SLOTS", []) or []:
        for raw in slot.get("tickers", []) or []:
            ticker = str(raw or "").strip().upper()
            if ticker:
                tickers.add(ticker)
    return sorted(tickers)


def _parse_quality_penalty(notes: Sequence[Any]) -> float:
    for note in notes or ():
        text = str(note or "")
        if not text.startswith("quality_penalty_pct="):
            continue
        try:
            return float(text.split("=", 1)[1].strip())
        except ValueError:
            return 0.0
    return 0.0


def build_ranked_candidates(report: Mapping[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    """Build sorted candidates from a shadow report.

    Ranked candidates include only scored rows with actionable outcomes
    (`auto_execute_buy`, `flag_for_review`, `short_candidate`).
    """
    rows = report.get("results") if isinstance(report, Mapping) else None
    if not isinstance(rows, list):
        return []

    ranked: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "")
        if not status.startswith("scored"):
            continue

        action = str(row.get("action") or DecisionAction.NO_ACTION.value)
        if action == DecisionAction.NO_ACTION.value:
            continue

        try:
            final_score = float(row.get("final_score") or 0.0)
        except (TypeError, ValueError):
            final_score = 0.0

        directional_score = final_score
        if action == DecisionAction.SHORT_CANDIDATE.value:
            directional_score = 100.0 - final_score

        freshness = row.get("freshness") if isinstance(row.get("freshness"), Mapping) else {}
        warning_layers = list(freshness.get("warning_layers") or [])
        stale_layers = list(freshness.get("stale_layers") or [])
        missing_required = list(row.get("missing_required_layers") or [])
        notes = list(row.get("notes") or [])
        quality_penalty_pct = _parse_quality_penalty(notes)

        ranked.append(
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "action": action,
                "status": status,
                "final_score": round(final_score, 4),
                "weighted_score": float(row.get("weighted_score") or 0.0),
                "layer_count": int(row.get("layer_count") or 0),
                "missing_required_layers": missing_required,
                "warning_layers": warning_layers,
                "stale_layers": stale_layers,
                "quality_penalty_pct": quality_penalty_pct,
                "rank_score": round(directional_score, 4),
            }
        )

    ranked.sort(
        key=lambda item: (
            item["rank_score"],
            item["final_score"],
            -len(item["stale_layers"]),
            -len(item["warning_layers"]),
        ),
        reverse=True,
    )

    limited = ranked[: max(1, int(limit))]
    for idx, item in enumerate(limited, start=1):
        item["rank"] = idx
    return limited


def summarize_freshness_diagnostics(report: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate freshness/missing diagnostics from shadow results."""
    rows = report.get("results") if isinstance(report, Mapping) else None
    if not isinstance(rows, list):
        return {
            "tickers_with_warnings": 0,
            "tickers_with_stale": 0,
            "total_warning_layers": 0,
            "total_stale_layers": 0,
            "scored_missing_required_layers": 0,
            "blocked_missing_required_layers": 0,
            "blocked_stale_layers": 0,
        }

    tickers_with_warnings = 0
    tickers_with_stale = 0
    total_warning_layers = 0
    total_stale_layers = 0
    scored_missing_required = 0
    blocked_missing_required = 0
    blocked_stale = 0

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "")
        freshness = row.get("freshness") if isinstance(row.get("freshness"), Mapping) else {}
        warning_layers = list(freshness.get("warning_layers") or [])
        stale_layers = list(freshness.get("stale_layers") or [])
        missing_required = list(row.get("missing_required_layers") or [])

        if warning_layers:
            tickers_with_warnings += 1
        if stale_layers:
            tickers_with_stale += 1
        total_warning_layers += len(warning_layers)
        total_stale_layers += len(stale_layers)

        if status.startswith("scored") and missing_required:
            scored_missing_required += 1
        if status == "blocked_missing_required_layers":
            blocked_missing_required += 1
        if status == "blocked_stale_layers":
            blocked_stale += 1

    return {
        "tickers_with_warnings": tickers_with_warnings,
        "tickers_with_stale": tickers_with_stale,
        "total_warning_layers": total_warning_layers,
        "total_stale_layers": total_stale_layers,
        "scored_missing_required_layers": scored_missing_required,
        "blocked_missing_required_layers": blocked_missing_required,
        "blocked_stale_layers": blocked_stale,
    }


def enrich_signal_shadow_payload(payload: Mapping[str, Any], ranking_limit: int = 20) -> dict[str, Any]:
    """Attach ranking and freshness diagnostics to the shadow payload."""
    output = dict(payload or {})
    if not output.get("has_report"):
        return output

    report = output.get("report")
    if not isinstance(report, Mapping):
        return output

    report_copy = dict(report)
    ranked = build_ranked_candidates(report_copy, limit=ranking_limit)
    freshness = summarize_freshness_diagnostics(report_copy)
    report_copy["ranked_candidates"] = ranked
    output["report"] = report_copy
    output["freshness_diagnostics"] = freshness
    return output


def run_tier1_shadow_jobs(
    db_path: str = DB_PATH,
    tickers: Optional[Sequence[str]] = None,
    as_of: str = "",
    config_obj: Tier1ShadowJobsConfig = Tier1ShadowJobsConfig(),
    sa_quant_runner: Optional[SAQuantJobRunner] = None,
    shadow_runner: Callable[..., dict[str, Any]] = run_signal_shadow_cycle,
    now_fn: Callable[[], str] = _utc_now_iso,
) -> dict[str, Any]:
    """Run tier-1 refresh jobs and a strict shadow cycle for ranking output."""
    run_at = as_of.strip() or now_fn()
    run_id = uuid.uuid4().hex[:12]
    source_tickers = tickers if tickers is not None else _extract_strategy_tickers()
    normalized_tickers = sorted({str(item or "").strip().upper() for item in source_tickers if str(item or "").strip()})

    layer_jobs: dict[str, dict[str, Any]] = {
        layer_id.value: {
            "status": "skipped",
            "detail": "ingest runner not configured",
        }
        for layer_id in LAYER_ORDER
    }

    # ── L8: SA Quant ────────────────────────────────────────────────────
    runner = sa_quant_runner or SAQuantJobRunner(db_path=db_path)
    sa_quant_summary: dict[str, Any] = {}
    try:
        sa_quant_summary = runner.run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L8_SA_QUANT.value] = {
            "status": "completed",
            "detail": (
                f"success={int(sa_quant_summary.get('tickers_success', 0))}, "
                f"failed={int(sa_quant_summary.get('tickers_failed', 0))}"
            ),
            "job_id": sa_quant_summary.get("job_id"),
            "tickers_total": int(sa_quant_summary.get("tickers_total", 0)),
            "tickers_success": int(sa_quant_summary.get("tickers_success", 0)),
            "tickers_failed": int(sa_quant_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L8_SA_QUANT.value] = {
            "status": "failed",
            "detail": str(exc),
        }

    # ── L1: PEAD (Post-Earnings Announcement Drift) ──────────────────
    try:
        from intelligence.jobs.pead_job import PEADJobRunner
        pead_summary = PEADJobRunner(db_path=db_path).run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L1_PEAD.value] = {
            "status": "completed",
            "detail": f"success={pead_summary.get('tickers_success', 0)}, failed={pead_summary.get('tickers_failed', 0)}",
            "job_id": pead_summary.get("job_id"),
            "tickers_success": int(pead_summary.get("tickers_success", 0)),
            "tickers_failed": int(pead_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L1_PEAD.value] = {"status": "failed", "detail": str(exc)}

    # ── L2: Insider Buying ───────────────────────────────────────────
    try:
        from intelligence.jobs.insider_job import InsiderJobRunner
        insider_summary = InsiderJobRunner(db_path=db_path).run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L2_INSIDER.value] = {
            "status": "completed",
            "detail": f"success={insider_summary.get('tickers_success', 0)}, failed={insider_summary.get('tickers_failed', 0)}",
            "job_id": insider_summary.get("job_id"),
            "tickers_success": int(insider_summary.get("tickers_success", 0)),
            "tickers_failed": int(insider_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L2_INSIDER.value] = {"status": "failed", "detail": str(exc)}

    # ── L4: Analyst Revisions ────────────────────────────────────────
    try:
        from intelligence.jobs.analyst_job import AnalystJobRunner
        analyst_summary = AnalystJobRunner(db_path=db_path).run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L4_ANALYST_REVISIONS.value] = {
            "status": "completed",
            "detail": f"success={analyst_summary.get('tickers_success', 0)}, failed={analyst_summary.get('tickers_failed', 0)}",
            "job_id": analyst_summary.get("job_id"),
            "tickers_success": int(analyst_summary.get("tickers_success", 0)),
            "tickers_failed": int(analyst_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L4_ANALYST_REVISIONS.value] = {"status": "failed", "detail": str(exc)}

    # ── L5: Congressional Trading ────────────────────────────────────
    try:
        from intelligence.jobs.congressional_job import CongressionalJobRunner
        cong_summary = CongressionalJobRunner(db_path=db_path).run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L5_CONGRESSIONAL.value] = {
            "status": "completed",
            "detail": f"success={cong_summary.get('tickers_success', 0)}, failed={cong_summary.get('tickers_failed', 0)}",
            "job_id": cong_summary.get("job_id"),
            "tickers_success": int(cong_summary.get("tickers_success", 0)),
            "tickers_failed": int(cong_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L5_CONGRESSIONAL.value] = {"status": "failed", "detail": str(exc)}

    # ── L6: News Sentiment ───────────────────────────────────────────
    try:
        from intelligence.jobs.news_job import NewsJobRunner
        news_summary = NewsJobRunner(db_path=db_path).run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L6_NEWS_SENTIMENT.value] = {
            "status": "completed",
            "detail": f"success={news_summary.get('tickers_success', 0)}, failed={news_summary.get('tickers_failed', 0)}",
            "job_id": news_summary.get("job_id"),
            "tickers_success": int(news_summary.get("tickers_success", 0)),
            "tickers_failed": int(news_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L6_NEWS_SENTIMENT.value] = {"status": "failed", "detail": str(exc)}

    # ── L7: Technical Overlay ────────────────────────────────────────
    try:
        from intelligence.jobs.technical_job import TechnicalJobRunner
        tech_summary = TechnicalJobRunner(db_path=db_path).run(tickers=normalized_tickers, as_of=run_at)
        layer_jobs[LayerId.L7_TECHNICAL.value] = {
            "status": "completed",
            "detail": f"success={tech_summary.get('tickers_success', 0)}, failed={tech_summary.get('tickers_failed', 0)}",
            "job_id": tech_summary.get("job_id"),
            "tickers_success": int(tech_summary.get("tickers_success", 0)),
            "tickers_failed": int(tech_summary.get("tickers_failed", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        layer_jobs[LayerId.L7_TECHNICAL.value] = {"status": "failed", "detail": str(exc)}

    shadow_report = shadow_runner(
        db_path=db_path,
        required_layers=config_obj.required_layers,
        min_layers_for_score=config_obj.min_layers_for_score,
        enforce_required_layers=config_obj.enforce_required_layers,
        now_fn=lambda: run_at,
    )
    ranked = build_ranked_candidates(shadow_report, limit=config_obj.ranking_limit)
    freshness = summarize_freshness_diagnostics(shadow_report)

    return {
        "run_id": run_id,
        "run_at": run_at,
        "tickers": normalized_tickers,
        "required_layers": [layer.value for layer in config_obj.required_layers],
        "layer_jobs": layer_jobs,
        "sa_quant_summary": sa_quant_summary,
        "shadow_report": shadow_report,
        "ranked_candidates": ranked,
        "freshness_diagnostics": freshness,
        "result_json": json.dumps(
            {
                "run_id": run_id,
                "run_at": run_at,
                "ranked_candidates": ranked,
                "freshness_diagnostics": freshness,
                "summary": shadow_report.get("summary", {}),
            },
            sort_keys=True,
        ),
    }
