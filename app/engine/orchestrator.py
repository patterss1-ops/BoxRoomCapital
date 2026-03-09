"""Strategy orchestration engine — wires signals to execution intents.

C-001: Runs registered strategies, converts signals to OrderIntents,
validates through account routing, pre-trade risk, and persists approved
intents for downstream execution.
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.signal.ai_confidence import (
    AIConfidenceGateConfig,
    ExecutionQualitySnapshot,
)
from app.signal.ai_contracts import PanelConsensus
from data.trade_db import DB_PATH, get_conn
from fund.promotion_gate import (
    PromotionGateConfig,
    evaluate_promotion_gate,
)
from execution.order_intent import OrderIntent
from execution.policy.capability_policy import StrategyRequirements
from execution.policy.ai_gate_policy import (
    AIGatePolicyInput,
    evaluate_ai_gate_policy,
)
from execution.policy.route_policy import RoutePolicyState
from execution.router import AccountRouter, RouteIntent
from execution.signal_adapter import StrategySlotConfig, signal_to_order_intent
from risk.pre_trade_gate import (
    RiskContext,
    RiskLimits,
    RiskOrderRequest,
    evaluate_pre_trade_risk,
)
from strategies.base import BaseStrategy, Signal, SignalType
from utils.datetime_utils import utc_now_naive_iso

logger = logging.getLogger(__name__)


# ─── Default risk limits (conservative) ──────────────────────────────────

DEFAULT_RISK_LIMITS = RiskLimits(
    max_position_pct_equity=15.0,
    max_sleeve_pct_equity=40.0,
    max_correlated_pct_equity=60.0,
)


@dataclass
class OrchestrationResult:
    """Summary of one orchestration cycle."""

    run_id: str
    run_at: str
    signals: list[dict] = field(default_factory=list)
    intents_created: list[dict] = field(default_factory=list)
    intents_rejected: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    macro_regime: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_at": self.run_at,
            "signals_total": len(self.signals),
            "intents_created": len(self.intents_created),
            "intents_rejected": len(self.intents_rejected),
            "errors": len(self.errors),
            "macro_regime": self.macro_regime,
        }


@dataclass
class StrategySlot:
    """A strategy instance bound to its execution config and tickers."""

    strategy: BaseStrategy
    config: StrategySlotConfig
    tickers: list[str]
    requirements: StrategyRequirements = field(
        default_factory=StrategyRequirements,
    )


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


def _get_macro_regime():
    """Attempt to classify current macro regime from FeatureStore data.

    Returns MacroRegimeResult or None if data is unavailable.
    """
    try:
        from intelligence.feature_store import FeatureStore
        from intelligence.macro_regime import MacroRegimeClassifier

        fs = FeatureStore()
        try:
            classifier = MacroRegimeClassifier(feature_store=fs)
            return classifier.classify()
        finally:
            fs.close()
    except Exception as exc:
        logger.debug("Macro regime classification unavailable: %s", exc)
        return None


def _build_risk_context(equity: float, db_path: str) -> dict:
    """Build mutable risk context dicts from current portfolio state.

    Returns a plain dict (not frozen RiskContext) so the orchestrator can
    accumulate intra-cycle exposure as intents are approved.
    """
    conn = get_conn(db_path)

    ticker_rows = conn.execute(
        """SELECT UPPER(bp.ticker) as ticker,
                  SUM(ABS(CAST(bp.market_value AS REAL))) as exposure
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1
           GROUP BY UPPER(bp.ticker)"""
    ).fetchall()
    ticker_exposure = {r["ticker"]: float(r["exposure"]) for r in ticker_rows}

    sleeve_rows = conn.execute(
        """SELECT COALESCE(bp.sleeve, 'unassigned') as sleeve,
                  SUM(ABS(CAST(bp.market_value AS REAL))) as exposure
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1
           GROUP BY COALESCE(bp.sleeve, 'unassigned')"""
    ).fetchall()
    sleeve_exposure = {r["sleeve"]: float(r["exposure"]) for r in sleeve_rows}

    conn.close()

    return {
        "equity": equity if equity > 0 else 1.0,
        "ticker_exposure": ticker_exposure,
        "sleeve_exposure": sleeve_exposure,
    }


def _make_frozen_risk_context(ctx: dict) -> RiskContext:
    """Snapshot the mutable risk context into a frozen RiskContext."""
    return RiskContext(
        equity=ctx["equity"],
        ticker_exposure_notional=dict(ctx["ticker_exposure"]),
        sleeve_exposure_notional=dict(ctx["sleeve_exposure"]),
    )


def _estimate_notional(
    intent: OrderIntent,
    universe_data: dict[str, Any],
) -> float:
    """Estimate order notional from the latest close price × quantity.

    Falls back to qty alone if no price data is available.
    """
    df = universe_data.get(intent.instrument)
    if df is not None and hasattr(df, "iloc") and len(df) > 0:
        try:
            last_close = float(df["Close"].iloc[-1])
            return intent.qty * last_close
        except (KeyError, IndexError, TypeError):
            pass
    # Fallback: qty is the notional (e.g., spreadbet stake-per-point)
    return intent.qty


def _prefetch_universe_data(
    slots: list[StrategySlot],
    data_provider: Any,
) -> dict[str, Any]:
    """Pre-fetch OHLC data for all tickers across all slots.

    Returns a dict of ticker -> DataFrame.  Strategies like DualMomentum
    require cross-asset data (universe_data kwarg) so we fetch everything
    up front and pass it through.
    """
    all_tickers: set[str] = set()
    for slot in slots:
        all_tickers.update(slot.tickers)
    universe: dict[str, Any] = {}
    for ticker in all_tickers:
        try:
            df = data_provider.get_daily_bars(ticker)
            if df is not None and not df.empty:
                universe[ticker] = df
        except Exception as exc:
            logger.warning("Failed to fetch data for %s: %s", ticker, exc)
    return universe


def run_orchestration_cycle(
    slots: list[StrategySlot],
    db_path: str = DB_PATH,
    dry_run: bool = False,
    data_provider: Any = None,
    equity: float = 0.0,
    risk_limits: Optional[RiskLimits] = None,
    router: Optional[AccountRouter] = None,
    policy_state: Optional[RoutePolicyState] = None,
    ai_consensus_by_ticker: Optional[dict[str, PanelConsensus]] = None,
    ai_execution_quality: Optional[ExecutionQualitySnapshot] = None,
    ai_gate_config: AIConfidenceGateConfig = AIConfidenceGateConfig(),
    promotion_gate_config: Optional[PromotionGateConfig] = None,
) -> OrchestrationResult:
    """Run one orchestration cycle across all registered strategy slots.

    For each slot, for each ticker:
      1. Fetch current position from the broker ledger
      2. Fetch OHLC data via the data provider
      3. Generate a signal from the strategy (with universe_data)
      4. If actionable, convert to OrderIntent
      5. Validate through account router (if provided)
      6. Validate through pre-trade risk gate (entries only; exits always pass)
      7. Validate through promotion gate (entries only; exits bypass)
      8. Persist the intent (or log as shadow trade if dry_run)

    The risk context accumulates intra-cycle: each approved intent's
    notional is added to ticker/sleeve exposure before evaluating the next.

    Args:
        slots: Strategy slots to execute.
        db_path: Database path for position lookups and intent persistence.
        dry_run: If True, log shadow trades instead of persisting intents.
        data_provider: DataProvider instance for OHLC bars (lazy-imported if None).
        equity: Current fund equity for risk calculations. If 0, risk gate is skipped.
        risk_limits: Hard risk limits. Defaults to conservative limits.
        router: Optional AccountRouter for route/capability validation.
        policy_state: Optional RoutePolicyState (kill switch, cooldowns).
        ai_consensus_by_ticker: Optional ticker->PanelConsensus map.
        ai_execution_quality: Optional execution quality calibration snapshot.
        ai_gate_config: AI confidence gate behavior config.
        promotion_gate_config: Optional promotion lane enforcement config.

    Returns:
        OrchestrationResult summarising all signals, intents, and errors.
    """
    run_id = uuid.uuid4().hex[:12]
    run_at = utc_now_naive_iso()
    result = OrchestrationResult(run_id=run_id, run_at=run_at)

    if data_provider is None:
        from data.provider import DataProvider
        data_provider = DataProvider()

    limits = risk_limits or DEFAULT_RISK_LIMITS

    # Build mutable risk context if equity is provided
    risk_ctx_dict: Optional[dict] = None
    if equity > 0:
        risk_ctx_dict = _build_risk_context(equity, db_path)

    # Consult macro regime — adjust risk context when risk_off
    macro_regime_result = _get_macro_regime()
    if risk_ctx_dict and macro_regime_result:
        result.macro_regime = macro_regime_result.regime.value
        if macro_regime_result.regime.value == "risk_off":
            logger.info(
                "Macro regime is RISK_OFF (%s) — reducing position sizes 50%%",
                macro_regime_result.reason,
            )
            risk_ctx_dict["macro_size_factor"] = 0.5
        elif macro_regime_result.regime.value == "transition":
            risk_ctx_dict["macro_size_factor"] = 0.75
        else:
            risk_ctx_dict["macro_size_factor"] = 1.0

    # Pre-fetch all universe data so cross-asset strategies get what they need
    universe_data = _prefetch_universe_data(slots, data_provider)

    for slot in slots:
        for ticker in slot.tickers:
            try:
                _process_one_signal(
                    slot=slot,
                    ticker=ticker,
                    result=result,
                    db_path=db_path,
                    dry_run=dry_run,
                    universe_data=universe_data,
                    risk_ctx_dict=risk_ctx_dict,
                    risk_limits=limits,
                    router=router,
                    policy_state=policy_state,
                    ai_consensus_by_ticker=ai_consensus_by_ticker,
                    ai_execution_quality=ai_execution_quality,
                    ai_gate_config=ai_gate_config,
                    promotion_gate_config=promotion_gate_config,
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
    universe_data: dict[str, Any],
    risk_ctx_dict: Optional[dict],
    risk_limits: RiskLimits,
    router: Optional[AccountRouter],
    policy_state: Optional[RoutePolicyState],
    ai_consensus_by_ticker: Optional[dict[str, PanelConsensus]],
    ai_execution_quality: Optional[ExecutionQualitySnapshot],
    ai_gate_config: AIConfidenceGateConfig,
    promotion_gate_config: Optional[PromotionGateConfig] = None,
) -> None:
    """Generate and process one signal for a (strategy, ticker) pair."""
    position = _get_current_position(ticker, db_path)
    bars = _get_bars_in_trade(ticker, slot.config.strategy_id, db_path)

    df = universe_data.get(ticker)
    if df is None or (hasattr(df, "empty") and df.empty):
        raise ValueError(f"No OHLC data available for {ticker}")

    signal = slot.strategy.generate_signal(
        ticker=ticker,
        df=df,
        current_position=position,
        bars_in_trade=bars,
        universe_data=universe_data,
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
    is_exit = signal.signal_type in (SignalType.LONG_EXIT, SignalType.SHORT_EXIT)

    # ── Account router validation ────────────────────────────────────────
    if router is not None:
        route_intent = RouteIntent(
            strategy_id=slot.config.strategy_id,
            sleeve=slot.config.sleeve,
            ticker=ticker,
            requirements=slot.requirements,
        )
        route_decision = router.resolve(route_intent, policy_state=policy_state)
        if not route_decision.allowed:
            result.intents_rejected.append({
                **intent.to_payload(),
                "reject_rule": route_decision.reason_code,
                "reject_message": route_decision.message,
            })
            logger.warning(
                "Router rejected %s/%s: %s — %s",
                slot.config.strategy_id, ticker,
                route_decision.reason_code, route_decision.message,
            )
            return
        # Apply resolved broker/account lane so persisted payload matches
        # the deterministic routing decision.
        if route_decision.resolution is not None:
            intent.account_type = route_decision.resolution.account_type
            intent.broker_target = route_decision.resolution.broker_name

    # ── Pre-trade risk gate (entries only — exits always pass) ───────────
    if risk_ctx_dict is not None and not is_exit:
        notional = _estimate_notional(intent, universe_data)
        risk_ctx = _make_frozen_risk_context(risk_ctx_dict)
        risk_request = RiskOrderRequest(
            ticker=ticker,
            sleeve=slot.config.sleeve,
            order_exposure_notional=notional,
        )
        decision = evaluate_pre_trade_risk(
            request=risk_request,
            context=risk_ctx,
            limits=risk_limits,
        )
        if not decision.approved:
            result.intents_rejected.append({
                **intent.to_payload(),
                "reject_rule": decision.rule_id,
                "reject_message": decision.message,
            })
            logger.warning(
                "Risk gate rejected %s/%s: %s — %s",
                slot.config.strategy_id, ticker,
                decision.rule_id, decision.message,
            )
            return

        # ── Accumulate intra-cycle exposure ──────────────────────────────
        tk = ticker.upper()
        risk_ctx_dict["ticker_exposure"][tk] = (
            risk_ctx_dict["ticker_exposure"].get(tk, 0.0) + notional
        )
        risk_ctx_dict["sleeve_exposure"][slot.config.sleeve] = (
            risk_ctx_dict["sleeve_exposure"].get(slot.config.sleeve, 0.0) + notional
        )

    # ── Promotion gate (entries only — exits bypass) ────────────────────
    if promotion_gate_config is not None:
        promo_decision = evaluate_promotion_gate(
            strategy_key=slot.config.strategy_id,
            is_exit=is_exit,
            config=promotion_gate_config,
            db_path=db_path,
        )
        if not promo_decision.allowed:
            result.intents_rejected.append({
                **intent.to_payload(),
                "reject_rule": promo_decision.reason_code,
                "reject_message": promo_decision.message,
            })
            logger.warning(
                "Promotion gate rejected %s/%s: %s — %s",
                slot.config.strategy_id,
                ticker,
                promo_decision.reason_code,
                promo_decision.message,
            )
            return

    # ── AI confidence gate (entries only — exits bypass) ─────────────────
    if not is_exit and ai_consensus_by_ticker is not None:
        consensus = ai_consensus_by_ticker.get(ticker.upper())
        if consensus is not None:
            ai_decision = evaluate_ai_gate_policy(
                AIGatePolicyInput(
                    consensus=consensus,
                    execution_quality=ai_execution_quality,
                    config=ai_gate_config,
                )
            )
            if not ai_decision.allowed:
                result.intents_rejected.append({
                    **intent.to_payload(),
                    "reject_rule": ai_decision.reason_code,
                    "reject_message": ai_decision.message,
                    "ai_calibrated_confidence": ai_decision.calibrated_confidence,
                    "ai_min_required_confidence": ai_decision.min_required_confidence,
                })
                logger.warning(
                    "AI confidence gate rejected %s/%s: %s — %s",
                    slot.config.strategy_id,
                    ticker,
                    ai_decision.reason_code,
                    ai_decision.message,
                )
                return

    if dry_run:
        from data.trade_db import log_shadow_trade
        log_shadow_trade(
            ticker=ticker,
            strategy=slot.config.strategy_id,
            action="open" if not is_exit else "close",
            size=intent.qty,
            reason=signal.reason,
            db_path=db_path,
        )
        result.intents_created.append({
            **intent.to_payload(),
            "dry_run": True,
        })
        return

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
