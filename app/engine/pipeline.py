"""Strategy registry + pipeline wiring for the multi-strategy orchestrator.

D-001: Config-driven strategy slot instantiation + dispatch callback that
wires scheduler → orchestrator.

Architecture overview:

    config.STRATEGY_SLOTS (config.py)
        │
        ▼
    build_strategy_slots()  ──→  list[StrategySlot]
        │                              │
        ▼                              ▼
    dispatch_orchestration()  ──→  run_orchestration_cycle()
        ▲                              │
        │                              ▼
    DailyWorkflowScheduler       OrchestrationResult
        (scheduler.py)              (.summary() for scheduler)

Key entry points:
    build_strategy_slots()      — parse STRATEGY_SLOTS config → StrategySlot list
    dispatch_orchestration()    — scheduler callback → orchestrator cycle
    register_strategy_class()   — add strategy classes to the registry
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional, Type

from app.signal.ai_confidence import AIConfidenceGateConfig, ExecutionQualitySnapshot
from app.signal.ai_contracts import PanelConsensus
from data.trade_db import DB_PATH, get_conn
from execution.policy.capability_policy import StrategyRequirements
from execution.signal_adapter import StrategySlotConfig
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


# ─── Strategy class registry ────────────────────────────────────────────

_STRATEGY_CLASS_REGISTRY: dict[str, Type[BaseStrategy]] = {}


def register_strategy_class(name: str, cls: Type[BaseStrategy]) -> None:
    """Register a strategy class by name for config-driven instantiation.

    Args:
        name: Registry key (e.g. "GTAAStrategy").  Must match the
              strategy_class field in STRATEGY_SLOTS config entries.
        cls:  BaseStrategy subclass.  Must accept ``params`` kwarg.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Strategy class name must be a non-empty string")
    _STRATEGY_CLASS_REGISTRY[name] = cls
    logger.debug("Registered strategy class: %s -> %s", name, cls.__name__)


def get_registered_strategies() -> dict[str, Type[BaseStrategy]]:
    """Return a copy of the current strategy class registry."""
    return dict(_STRATEGY_CLASS_REGISTRY)


def _ensure_default_registry() -> None:
    """Populate the default strategy registry if empty.

    Lazy-loaded to avoid import cycles and to allow test isolation
    (tests can clear the registry and register mocks without pulling
    in the full strategy dependency tree).
    """
    if _STRATEGY_CLASS_REGISTRY:
        return

    # Import here to avoid circular imports at module load time
    from strategies.dual_momentum import DualMomentumStrategy
    from strategies.gtaa import GTAAStrategy

    register_strategy_class("GTAAStrategy", GTAAStrategy)
    register_strategy_class("DualMomentumStrategy", DualMomentumStrategy)
    logger.debug("Default strategy registry populated with %d classes",
                 len(_STRATEGY_CLASS_REGISTRY))


def clear_registry() -> None:
    """Clear the strategy class registry.

    Intended for test isolation — production code should not call this.
    """
    _STRATEGY_CLASS_REGISTRY.clear()


# ─── Slot building ──────────────────────────────────────────────────────

_REQUIRED_FIELDS = frozenset({
    "id", "strategy_class", "strategy_version", "tickers",
    "sleeve", "account_type", "broker_target", "base_qty",
})


def build_strategy_slots(
    slot_configs: Optional[list[dict[str, Any]]] = None,
) -> list:
    """Parse strategy slot configurations into StrategySlot objects.

    Args:
        slot_configs: List of slot config dicts.  If None, reads from
                      ``config.STRATEGY_SLOTS``.

    Each config dict must contain:
        id               — Unique slot identifier (e.g. "gtaa_isa")
        strategy_class   — Registry key (e.g. "GTAAStrategy")
        strategy_version — Semver string (e.g. "1.0")
        tickers          — List of ticker symbols
        sleeve           — Portfolio sleeve name
        account_type     — Account routing lane (ISA, GIA, SPREADBET, etc.)
        broker_target    — Target broker (ig, ibkr, paper)
        base_qty         — Base position quantity

    Optional fields:
        params           — Strategy constructor params dict (default: {})
        risk_tags        — List of risk tag strings (default: [])
        requirements     — Dict of StrategyRequirements fields (default: {})
        enabled          — Boolean (default: True)

    Returns:
        List of StrategySlot objects ready for ``run_orchestration_cycle()``.

    Raises:
        ValueError: If required fields are missing or strategy class unknown.
    """
    # Lazy import to avoid circular dependency (orchestrator imports
    # strategies.base, and pipeline imports orchestrator)
    from app.engine.orchestrator import StrategySlot

    _ensure_default_registry()

    if slot_configs is None:
        import config
        slot_configs = getattr(config, "STRATEGY_SLOTS", [])

    slots: list[StrategySlot] = []

    for raw in slot_configs:
        if not raw.get("enabled", True):
            logger.info("Skipping disabled slot: %s", raw.get("id", "?"))
            continue

        slot = _parse_one_slot(raw)
        slots.append(slot)
        logger.info(
            "Built slot '%s': %s on %s (%s/%s)",
            slot.config.strategy_id,
            type(slot.strategy).__name__,
            slot.tickers,
            slot.config.broker_target,
            slot.config.account_type,
        )

    logger.info("Built %d strategy slots from config", len(slots))
    return slots


def _parse_one_slot(raw: dict[str, Any]) -> Any:
    """Parse a single slot config dict into a StrategySlot.

    Raises ValueError on missing fields or unknown strategy class.
    """
    from app.engine.orchestrator import StrategySlot

    # Validate required fields
    missing = _REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise ValueError(
            f"Strategy slot config missing required fields: {sorted(missing)}"
        )

    slot_id = raw["id"]
    class_name = raw["strategy_class"]

    # Look up strategy class in registry
    strategy_cls = _STRATEGY_CLASS_REGISTRY.get(class_name)
    if strategy_cls is None:
        raise ValueError(
            f"Unknown strategy class '{class_name}' in slot '{slot_id}'. "
            f"Registered: {sorted(_STRATEGY_CLASS_REGISTRY.keys())}"
        )

    # Instantiate strategy with params
    params = raw.get("params", {})
    strategy = strategy_cls(params=params)

    # Build execution config
    slot_config = StrategySlotConfig(
        strategy_id=slot_id,
        strategy_version=raw["strategy_version"],
        sleeve=raw["sleeve"],
        account_type=raw["account_type"],
        broker_target=raw["broker_target"],
        base_qty=float(raw["base_qty"]),
        risk_tags=list(raw.get("risk_tags", [])),
    )

    # Build capability requirements
    req_dict = raw.get("requirements", {})
    requirements = StrategyRequirements(**req_dict)

    # Validate tickers
    tickers = list(raw["tickers"])
    if not tickers:
        raise ValueError(f"Slot '{slot_id}' has empty tickers list")

    return StrategySlot(
        strategy=strategy,
        config=slot_config,
        tickers=tickers,
        requirements=requirements,
    )


# ─── Equity helper ──────────────────────────────────────────────────────

def _get_fund_equity(db_path: str) -> float:
    """Attempt to read latest fund equity from DB.

    Returns 0.0 if unavailable (risk gate will be skipped).
    D-003 will provide a reliable live equity feed — until then,
    this is best-effort from fund_daily_report snapshots.
    """
    try:
        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT total_nav FROM fund_daily_report ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return float(row["total_nav"])
    except Exception:
        # Table may not exist yet, or no snapshots recorded
        pass
    return 0.0


def _build_execution_quality_snapshot(
    db_path: str,
    lookback_days: int = 14,
) -> Optional[ExecutionQualitySnapshot]:
    """Build an execution-quality snapshot for AI confidence calibration."""
    try:
        from fund.execution_quality import build_execution_quality_report

        report = build_execution_quality_report(
            window_days=max(1, int(lookback_days)),
            db_path=db_path,
        )
        return ExecutionQualitySnapshot(
            fill_rate_pct=float(report.fills.fill_rate_pct),
            reject_rate_pct=float(report.fills.reject_rate_pct),
            mean_slippage_bps=report.slippage.mean_bps,
            sample_count=int(report.fills.total_attempts),
        )
    except Exception as exc:
        logger.warning("Execution-quality snapshot unavailable: %s", exc)
        return None


def _collect_ai_panel_consensus(
    slots: list[Any],
    as_of: str,
) -> dict[str, PanelConsensus]:
    """Collect live AI panel consensus for all unique tickers in slots.

    Only clients with configured API keys are registered. Failures are logged
    and omitted so orchestration can continue with available data.
    """
    from intelligence.ai_panel import (
        ChatGPTClient,
        ClaudeClient,
        GeminiClient,
        GrokClient,
        PanelCoordinator,
    )

    coordinator = PanelCoordinator()
    registered = 0
    if os.getenv("XAI_API_KEY", "").strip():
        coordinator.register("grok", GrokClient().fetch_verdict)
        registered += 1
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        coordinator.register("claude", ClaudeClient().fetch_verdict)
        registered += 1
    if os.getenv("OPENAI_API_KEY", "").strip():
        coordinator.register("chatgpt", ChatGPTClient().fetch_verdict)
        registered += 1
    if os.getenv("GOOGLE_AI_API_KEY", "").strip():
        coordinator.register("gemini", GeminiClient().fetch_verdict)
        registered += 1

    if registered == 0:
        return {}

    tickers = sorted({str(t).upper() for slot in slots for t in slot.tickers})
    out: dict[str, PanelConsensus] = {}
    for ticker in tickers:
        try:
            out[ticker] = coordinator.query_panel(ticker=ticker, as_of=as_of)
        except Exception as exc:
            logger.warning("AI panel query failed for %s: %s", ticker, exc)
    return out


# ─── Dispatch callback for the scheduler ────────────────────────────────

def dispatch_orchestration(
    window_name: str,
    db_path: str = DB_PATH,
    dry_run: bool = False,
    slot_configs: Optional[list[dict[str, Any]]] = None,
    equity: Optional[float] = None,
    data_provider: Any = None,
    ai_consensus_by_ticker: Optional[dict[str, PanelConsensus]] = None,
    ai_execution_quality: Optional[ExecutionQualitySnapshot] = None,
    ai_gate_config: AIConfidenceGateConfig = AIConfidenceGateConfig(),
    ai_panel_enabled: bool = False,
    ai_execution_quality_lookback_days: int = 14,
) -> Any:
    """Dispatch callback wiring the scheduler to the orchestrator.

    This is the function passed as ``dispatch_fn`` to
    ``DailyWorkflowScheduler``.  It:

      1. Builds strategy slots from config (or slot_configs override)
      2. Reads fund equity from DB (or uses equity override)
      3. Calls ``run_orchestration_cycle()``
      4. Returns ``OrchestrationResult`` (which has ``.summary()``)

    The scheduler calls this with exactly three kwargs:
        window_name, db_path, dry_run

    Additional kwargs (slot_configs, equity, data_provider) exist for
    testing and manual invocation.

    Args:
        window_name: Scheduler window that triggered this dispatch.
        db_path: Database path.
        dry_run: If True, log shadow trades instead of persisting.
        slot_configs: Override slot configs (for testing).
        equity: Override fund equity (for testing).  If None, reads from DB.
        data_provider: DataProvider instance (lazy-loaded if None).
        ai_consensus_by_ticker: Optional explicit AI panel consensus map.
        ai_execution_quality: Optional explicit execution-quality snapshot.
        ai_gate_config: AI confidence gate behavior config.
        ai_panel_enabled: If True, fetch live panel consensus for slot tickers.
        ai_execution_quality_lookback_days: Rolling window for quality snapshot.

    Returns:
        OrchestrationResult with ``.summary()`` for the scheduler.
    """
    from app.engine.orchestrator import OrchestrationResult, run_orchestration_cycle

    logger.info(
        "Pipeline dispatch: window='%s', dry_run=%s", window_name, dry_run
    )

    slots = build_strategy_slots(slot_configs=slot_configs)

    if not slots:
        logger.warning(
            "No strategy slots configured — returning empty orchestration result"
        )
        return OrchestrationResult(
            run_id="empty",
            run_at=datetime.utcnow().isoformat(),
        )

    # Resolve equity: explicit override > DB lookup > 0 (risk gate skipped)
    resolved_equity = equity if equity is not None else _get_fund_equity(db_path)
    as_of = datetime.utcnow().isoformat()

    resolved_ai_consensus = ai_consensus_by_ticker
    if resolved_ai_consensus is None and ai_panel_enabled:
        resolved_ai_consensus = _collect_ai_panel_consensus(slots=slots, as_of=as_of)

    resolved_ai_quality = ai_execution_quality
    if resolved_ai_quality is None and (
        resolved_ai_consensus is not None or ai_panel_enabled
    ):
        resolved_ai_quality = _build_execution_quality_snapshot(
            db_path=db_path,
            lookback_days=ai_execution_quality_lookback_days,
        )

    result = run_orchestration_cycle(
        slots=slots,
        db_path=db_path,
        dry_run=dry_run,
        data_provider=data_provider,
        equity=resolved_equity,
        ai_consensus_by_ticker=resolved_ai_consensus,
        ai_execution_quality=resolved_ai_quality,
        ai_gate_config=ai_gate_config,
    )

    logger.info(
        "Pipeline dispatch complete: %d signals, %d intents, "
        "%d rejected, %d errors",
        len(result.signals),
        len(result.intents_created),
        len(result.intents_rejected),
        len(result.errors),
    )

    return result
