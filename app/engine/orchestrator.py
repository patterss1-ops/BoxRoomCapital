"""Strategy orchestration engine — wires signals to execution intents.

C-001: Runs registered strategies, converts signals to OrderIntents,
validates through routing and pre-trade risk, and persists approved
intents for downstream execution.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from data.trade_db import DB_PATH, get_conn
from execution.order_intent import OrderIntent
from execution.signal_adapter import StrategySlotConfig, signal_to_order_intent
from strategies.base import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Summary of one orchestration cycle."""

    run_id: str
    run_at: str
    signals: list[dict] = field(default_factory=list)
    intents_created: list[dict] = field(default_factory=list)
    intents_rejected: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_at": self.run_at,
            "signals_total": len(self.signals),
            "intents_created": len(self.intents_created),
            "intents_rejected": len(self.intents_rejected),
            "errors": len(self.errors),
        }


@dataclass
class StrategySlot:
    """A strategy instance bound to its execution config and tickers."""

    strategy: BaseStrategy
    config: StrategySlotConfig
    tickers: list[str]


def _get_current_position(ticker: str, db_path: str) -> float:
    """Look up net position for a ticker from the broker ledger."""
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT COALESCE(SUM(
               CASE WHEN direction = 'long' THEN CAST(quantity AS REAL)
                    WHEN direction = 'short' THEN -CAST(quantity AS REAL)
                    ELSE 0 END
           ), 0) as net_qty
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1 AND UPPER(bp.ticker) = UPPER(?)""",
        (ticker,),
    ).fetchone()
    conn.close()
    return float(row["net_qty"]) if row else 0.0


def _get_bars_in_trade(ticker: str, strategy_id: str, db_path: str) -> int:
    """Estimate bars held from the most recent order intent for this ticker/strategy.

    Returns 0 if no prior intent is found (conservative — strategy treats as new).
    """
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT created_at FROM order_intents
               WHERE instrument = ? AND strategy_id = ? AND status = 'completed'
               ORDER BY created_at DESC LIMIT 1""",
            (ticker, strategy_id),
        ).fetchone()
    except Exception:
        # Table may not exist yet (created lazily by order_intent_store)
        conn.close()
        return 0
    conn.close()
    if not row:
        return 0
    try:
        entry_dt = datetime.fromisoformat(row["created_at"])
        delta = datetime.now() - entry_dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


def run_orchestration_cycle(
    slots: list[StrategySlot],
    db_path: str = DB_PATH,
    dry_run: bool = False,
    data_provider: Any = None,
) -> OrchestrationResult:
    """Run one orchestration cycle across all registered strategy slots.

    For each slot, for each ticker:
      1. Fetch current position from the broker ledger
      2. Fetch OHLC data via the data provider
      3. Generate a signal from the strategy
      4. If actionable, convert to OrderIntent
      5. Persist the intent (or log as shadow trade if dry_run)

    Each slot runs in its own error boundary — one failure does not
    kill the cycle.

    Args:
        slots: Strategy slots to execute.
        db_path: Database path for position lookups and intent persistence.
        dry_run: If True, log shadow trades instead of persisting intents.
        data_provider: DataProvider instance for OHLC bars (lazy-imported if None).

    Returns:
        OrchestrationResult summarising all signals, intents, and errors.
    """
    run_id = uuid.uuid4().hex[:12]
    run_at = datetime.utcnow().isoformat()
    result = OrchestrationResult(run_id=run_id, run_at=run_at)

    if data_provider is None:
        from data.provider import DataProvider
        data_provider = DataProvider()

    for slot in slots:
        for ticker in slot.tickers:
            try:
                _process_one_signal(
                    slot=slot,
                    ticker=ticker,
                    result=result,
                    db_path=db_path,
                    dry_run=dry_run,
                    data_provider=data_provider,
                )
            except Exception as exc:
                error_entry = {
                    "strategy_id": slot.config.strategy_id,
                    "ticker": ticker,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
                result.errors.append(error_entry)
                logger.error(
                    "Orchestration error for %s/%s: %s",
                    slot.config.strategy_id, ticker, exc,
                )

    logger.info(
        "Orchestration cycle %s complete: %d signals, %d intents, %d rejected, %d errors",
        run_id,
        len(result.signals),
        len(result.intents_created),
        len(result.intents_rejected),
        len(result.errors),
    )
    return result


def _process_one_signal(
    slot: StrategySlot,
    ticker: str,
    result: OrchestrationResult,
    db_path: str,
    dry_run: bool,
    data_provider: Any,
) -> None:
    """Generate and process one signal for a (strategy, ticker) pair."""
    position = _get_current_position(ticker, db_path)
    bars = _get_bars_in_trade(ticker, slot.config.strategy_id, db_path)

    df = data_provider.get_daily_bars(ticker)
    if df is None or df.empty:
        raise ValueError(f"No OHLC data available for {ticker}")

    signal = slot.strategy.generate_signal(
        ticker=ticker,
        df=df,
        current_position=position,
        bars_in_trade=bars,
    )

    signal_entry = {
        "strategy_id": slot.config.strategy_id,
        "ticker": ticker,
        "signal_type": signal.signal_type.value,
        "reason": signal.reason,
        "size_multiplier": signal.size_multiplier,
    }
    result.signals.append(signal_entry)

    if signal.signal_type == SignalType.NONE:
        return

    intent = signal_to_order_intent(signal, slot.config)

    if dry_run:
        from data.trade_db import log_shadow_trade
        log_shadow_trade(
            ticker=ticker,
            strategy=slot.config.strategy_id,
            action="open" if signal.signal_type in (SignalType.LONG_ENTRY, SignalType.SHORT_ENTRY) else "close",
            size=intent.qty,
            reason=signal.reason,
            db_path=db_path,
        )
        result.intents_created.append({
            **intent.to_payload(),
            "dry_run": True,
        })
        return

    # Persist intent via the order intent store
    from data.order_intent_store import create_order_intent_envelope
    envelope = create_order_intent_envelope(
        intent=intent,
        action_type="orchestrator_cycle",
        actor="system",
        db_path=db_path,
    )
    result.intents_created.append({
        **intent.to_payload(),
        "intent_id": envelope["intent_id"],
    })
    logger.info(
        "Intent created: %s %s %s qty=%.2f [%s]",
        intent.side.value, ticker, slot.config.strategy_id,
        intent.qty, envelope["intent_id"],
    )
