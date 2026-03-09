"""Emit the current research readiness report as JSON."""

from __future__ import annotations

import json
import sys
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


def main() -> int:
    control = _build_control()
    print(
        json.dumps(
            build_research_readiness_report(
                pipeline_status=control.pipeline_status(),
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual operator entrypoint
    raise SystemExit(main())
