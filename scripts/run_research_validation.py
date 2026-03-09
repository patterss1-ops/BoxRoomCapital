"""Run one-shot DB-backed research validation and emit readiness JSON."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.control import BotControlService
from research.artifact_store import ArtifactStore
from research.readiness import build_research_readiness_report
from research.runtime import build_engine_a_pipeline, build_engine_b_pipeline
from research.shared.decay_review import DecayReviewService
from research.shared.kill_monitor import KillMonitor


def _build_control() -> BotControlService:
    control = BotControlService(PROJECT_ROOT)
    control.configure_research_services(
        engine_a_factory=lambda: build_engine_a_pipeline(),
        engine_b_factory=lambda: build_engine_b_pipeline(),
        decay_review_factory=lambda: DecayReviewService(artifact_store=ArtifactStore()),
        kill_monitor_factory=lambda: KillMonitor(artifact_store=ArtifactStore()),
    )
    return control


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DB-backed research validation")
    parser.add_argument(
        "--engine",
        choices=("engine_a", "engine_b", "all"),
        default="engine_a",
        help="Validation target to run.",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Override Engine A as_of timestamp (ISO-8601).",
    )
    parser.add_argument(
        "--raw-content",
        default="",
        help="Raw manual content for Engine B validation.",
    )
    parser.add_argument(
        "--source-class",
        default="news_wire",
        help="Engine B source class.",
    )
    parser.add_argument(
        "--source-credibility",
        type=float,
        default=0.7,
        help="Engine B source credibility in [0, 1].",
    )
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        default=[],
        help="Engine B source id. Repeat for multiple values.",
    )
    parser.add_argument(
        "--job-id",
        default="",
        help="Optional Engine B job id for traceability.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    control = _build_control()
    results: dict[str, object] = {}

    if args.engine in {"engine_a", "all"}:
        results["engine_a"] = control.run_engine_a_validation(as_of=args.as_of or None)

    if args.engine in {"engine_b", "all"}:
        if not args.raw_content.strip():
            raise SystemExit("--raw-content is required when validating Engine B")
        source_ids = [item.strip() for item in args.source_ids if item.strip()]
        if not source_ids:
            source_ids = [f"{args.source_class}:{uuid.uuid4().hex[:8]}"]
        results["engine_b"] = control.run_engine_b_validation(
            raw_content=args.raw_content.strip(),
            source_class=args.source_class.strip() or "news_wire",
            source_credibility=max(0.0, min(1.0, float(args.source_credibility))),
            source_ids=source_ids,
            job_id=args.job_id.strip() or None,
        )

    print(
        json.dumps(
            {
                "results": results,
                "readiness": build_research_readiness_report(
                    pipeline_status=control.pipeline_status(),
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual operator entrypoint
    raise SystemExit(main())
