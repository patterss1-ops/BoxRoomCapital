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
from datetime import datetime
from typing import Any, Optional, Type

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
    this is best-effort from fund_nav snapshots.
    """
    try:
        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT total_nav FROM fund_nav ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return float(row["total_nav"])
    except Exception:
        # Table may not exist yet, or no snapshots recorded
        pass
    return 0.0


# ─── Dispatch callback for the scheduler ────────────────────────────────

def dispatch_orchestration(
    window_name: str,
    db_path: str = DB_PATH,
    dry_run: bool = False,
    slot_configs: Optional[list[dict[str, Any]]] = None,
    equity: Optional[float] = None,
    data_provider: Any = None,
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

    result = run_orchestration_cycle(
        slots=slots,
        db_path=db_path,
        dry_run=dry_run,
        data_provider=data_provider,
        equity=resolved_equity,
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
