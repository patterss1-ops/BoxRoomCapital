"""Daily trading DAG — orchestrates the full signal-to-execution pipeline.

Uses the PipelineOrchestrator DAG engine to run the daily workflow:
  1. ingest_tier1    → run all signal layer data ingestion jobs
  2. signal_shadow   → run composite scoring for all tickers
  3. ai_panel        → query LLM consensus panel (optional)
  4. orchestration   → run strategy orchestration cycle
  5. dispatch        → dispatch approved intents to brokers (if not dry_run)
"""

from __future__ import annotations

import logging
from typing import Optional

import config
from data.pipeline_orchestrator import NodeConfig, PipelineOrchestrator, PipelineResult

logger = logging.getLogger(__name__)


def _run_ingest_tier1() -> None:
    """Run all signal layer ingestion jobs (L1-L8)."""
    from intelligence.jobs.signal_layer_jobs import run_tier1_shadow_jobs

    result = run_tier1_shadow_jobs()
    logger.info("Tier 1 ingest complete: %s", {
        k: v.get("status", "unknown") for k, v in result.get("layer_jobs", {}).items()
    })


def _run_signal_shadow() -> None:
    """Run composite scoring for top candidates via the shadow cycle."""
    from app.engine.signal_shadow import run_signal_shadow_cycle

    try:
        result = run_signal_shadow_cycle()
        scored = len(result.get("scores", [])) if isinstance(result, dict) else 0
        logger.info("Signal shadow scoring complete: %d tickers scored", scored)
    except Exception as exc:
        logger.warning("Signal shadow scoring failed (non-fatal): %s", exc)


def _run_ai_panel() -> None:
    """Run LLM consensus panel if enabled."""
    if not config.AI_PANEL_ENABLED:
        logger.info("AI panel disabled — skipping")
        return

    # The AI panel is invoked inside dispatch_orchestration when ai_panel_enabled=True
    logger.info("AI panel will run as part of orchestration dispatch")


def _run_orchestration() -> None:
    """Run the full orchestration cycle."""
    from app.engine.pipeline import dispatch_orchestration

    result = dispatch_orchestration(
        window_name="daily_dag",
        dry_run=config.ORCHESTRATOR_DRY_RUN,
        ai_panel_enabled=config.AI_PANEL_ENABLED,
    )
    logger.info(
        "Orchestration complete: %d signals, %d intents created, %d rejected",
        len(result.signals),
        len(result.intents_created),
        len(result.intents_rejected),
    )


def _run_dispatch() -> None:
    """Dispatch approved intents to brokers."""
    if config.ORCHESTRATOR_DRY_RUN or not config.DISPATCHER_ENABLED:
        logger.info("Dispatcher disabled or dry_run — skipping intent dispatch")
        return

    from execution.dispatcher import IntentDispatcher

    dispatcher = IntentDispatcher()
    result = dispatcher.run_once()
    logger.info("Intent dispatch complete: %s", result)


def build_daily_dag() -> PipelineOrchestrator:
    """Build and return the daily trading DAG (not yet executed)."""
    dag = PipelineOrchestrator()

    dag.add_node(NodeConfig(
        name="ingest_tier1",
        fn=_run_ingest_tier1,
        max_retries=1,
        retry_delay=5.0,
    ))

    dag.add_node(NodeConfig(
        name="signal_shadow",
        fn=_run_signal_shadow,
        dependencies=["ingest_tier1"],
    ))

    dag.add_node(NodeConfig(
        name="ai_panel",
        fn=_run_ai_panel,
        dependencies=["signal_shadow"],
    ))

    dag.add_node(NodeConfig(
        name="orchestration",
        fn=_run_orchestration,
        dependencies=["ai_panel"],
        max_retries=1,
        retry_delay=10.0,
    ))

    dag.add_node(NodeConfig(
        name="dispatch",
        fn=_run_dispatch,
        dependencies=["orchestration"],
    ))

    return dag


def run_daily_dag() -> PipelineResult:
    """Build and execute the daily trading DAG."""
    dag = build_daily_dag()

    errors = dag.validate()
    if errors:
        raise ValueError(f"DAG validation failed: {'; '.join(errors)}")

    logger.info("Starting daily trading DAG: %s", dag.get_execution_order())
    result = dag.run()
    logger.info(
        "Daily DAG finished: status=%s, duration=%.1fs",
        result.status.value,
        result.duration,
    )
    return result
