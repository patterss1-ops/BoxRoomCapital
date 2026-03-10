"""Reusable helpers for manual Engine A rebalance execution."""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass
from typing import Any, Callable

from broker.ig import IGBroker
import config
from data.order_intent_store import create_order_intent_envelope
from data.trade_db import DB_PATH
from execution.order_intent import OrderIntent, OrderSide
from execution.policy.capability_policy import RouteAccountType
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    ExecutionReport,
    InstrumentSpec,
    RebalanceSheet,
    RiskLimits as ResearchRiskLimits,
    SizingSpec,
    TradeSheet,
)
from research.market_data.futures import build_multiple_prices
from utils.datetime_utils import utc_now_iso

OrderIntentCreator = Callable[..., dict[str, Any]]
IGMarketDetailMap = dict[str, dict[str, Any]]

ENGINE_A_IG_PROXY_TICKERS: dict[str, str] = {
    "ES": "SPY",
    "NQ": "QQQ",
    "YM": "DIA",
    "RTY": "IWM",
    "ZF": "IEF",
    "ZN": "IEF",
    "ZB": "IEF",
    "GC": "GC=F",
    "SI": "SI=F",
    "CL": "CL=F",
    "NG": "NG=F",
    "HG": "HG=F",
    "6B": "GBPUSD=X",
}


@dataclass(frozen=True)
class ManualEngineAExecutionPreview:
    chain_id: str
    rebalance: ArtifactEnvelope
    deltas: dict[str, float]
    broker_target: str
    size_mode: str
    instruments: list[InstrumentSpec]


@dataclass(frozen=True)
class ManualEngineAExecutionResult:
    preview: ManualEngineAExecutionPreview
    approved_rebalance: ArtifactEnvelope
    trade_sheet: ArtifactEnvelope
    execution_report: ArtifactEnvelope
    queued_intents: list[dict[str, Any]]


def operator_created_by(actor: str) -> str:
    clean_actor = str(actor or "").strip() or "operator"
    return clean_actor if clean_actor.startswith("operator:") else f"operator:{clean_actor}"


def find_chain_artifact(
    chain_id: str,
    artifact_type: ArtifactType,
    *,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactEnvelope | None:
    store = artifact_store or ArtifactStore()
    chain = store.get_chain(chain_id)
    for envelope in reversed(chain):
        if envelope.artifact_type == artifact_type:
            return envelope
    return None


def latest_artifact_by_type(
    artifact_type: ArtifactType,
    *,
    engine: Engine,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactEnvelope | None:
    store = artifact_store or ArtifactStore()
    rows = store.query(
        artifact_type=artifact_type,
        engine=engine,
        limit=1,
    )
    return rows[0] if rows else None


def supersede_rebalance_sheet(
    *,
    rebalance: ArtifactEnvelope,
    approval_status: str,
    actor: str,
    notes: str,
    artifact_store: ArtifactStore,
) -> ArtifactEnvelope:
    clean_notes = str(notes or "").strip()
    body = dict(rebalance.body)
    body.update(
        {
            "approval_status": approval_status,
            "decision_source": "operator",
            "decided_by": str(actor or "").strip() or "operator",
            "operator_notes": clean_notes or None,
            "decided_at": utc_now_iso(),
        }
    )
    envelope = ArtifactEnvelope(
        artifact_type=ArtifactType.REBALANCE_SHEET,
        engine=rebalance.engine,
        ticker=rebalance.ticker,
        edge_family=rebalance.edge_family,
        chain_id=rebalance.chain_id,
        parent_id=rebalance.artifact_id,
        body=RebalanceSheet.model_validate(body),
        created_by=operator_created_by(actor),
        tags=list(rebalance.tags or []),
    )
    envelope.artifact_id = artifact_store.save(envelope)
    return envelope


def manual_engine_a_broker_target() -> str:
    mode = config.broker_mode()
    if mode == "paper":
        return "paper"

    is_demo = config.ig_broker_is_demo()
    if not config.ig_credentials_available(is_demo):
        env_name = "demo" if is_demo else "live"
        raise ValueError(f"IG {env_name} credentials are incomplete for research execution")
    return "ig"


def filter_manual_engine_a_deltas(
    deltas: dict[str, float],
    *,
    symbols: list[str] | None = None,
) -> dict[str, float]:
    requested = [
        str(symbol or "").strip().upper()
        for symbol in (symbols or [])
        if str(symbol or "").strip()
    ]
    if not requested:
        return dict(deltas)
    requested_set = set(requested)
    filtered = {
        instrument: float(delta)
        for instrument, delta in deltas.items()
        if str(instrument or "").strip().upper() in requested_set
    }
    if not filtered:
        joined = ", ".join(requested)
        raise ValueError(f"Requested Engine A symbols are not present in the rebalance: {joined}")
    return filtered


def resolve_manual_engine_a_size_mode(
    *,
    broker_target: str,
    size_mode: str = "auto",
) -> str:
    clean_mode = str(size_mode or "auto").strip().lower() or "auto"
    if clean_mode not in {"auto", "raw", "min"}:
        raise ValueError(f"Unsupported Engine A execution size mode '{size_mode}'")
    if clean_mode == "auto":
        return "min" if broker_target == "ig" else "raw"
    return clean_mode


def parse_contract_details(contract_details: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in str(contract_details or "").split(";"):
        key, separator, value = item.partition("=")
        if not separator:
            continue
        clean_key = key.strip().lower()
        clean_value = value.strip()
        if clean_key and clean_value:
            parsed[clean_key] = clean_value
    return parsed


def _extract_min_deal_size(market_info: dict[str, Any]) -> float:
    rules = market_info.get("dealingRules", {}) if isinstance(market_info, dict) else {}
    min_size = rules.get("minDealSize", {})
    if isinstance(min_size, dict):
        return float(min_size.get("value", 0) or 0)
    return float(min_size or 0)


def _extract_snapshot_reference_price(market_info: dict[str, Any]) -> float | None:
    snapshot = market_info.get("snapshot", {}) if isinstance(market_info, dict) else {}
    bid = float(snapshot.get("bid", 0) or 0)
    offer = float(snapshot.get("offer", 0) or 0)
    if bid > 0 and offer > 0:
        return (bid + offer) / 2.0
    if bid > 0:
        return bid
    if offer > 0:
        return offer
    return None


def _parse_as_of_date(as_of: str | None) -> date:
    text = str(as_of or "").strip()
    if not text:
        return datetime.utcnow().date()
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def fetch_engine_a_reference_prices(
    root_symbols: list[str],
    *,
    as_of: str | None = None,
) -> dict[str, float]:
    as_of_date = _parse_as_of_date(as_of)
    reference_prices: dict[str, float] = {}
    for root_symbol in sorted({str(item or "").strip().upper() for item in root_symbols if str(item or "").strip()}):
        try:
            multiple = build_multiple_prices(root_symbol, as_of_date)
        except Exception:
            continue
        if multiple is None:
            continue
        reference_price = float(getattr(multiple, "current_price", 0) or 0)
        if reference_price > 0:
            reference_prices[root_symbol] = reference_price
    return reference_prices


def fetch_ig_market_details(proxy_tickers: list[str]) -> IGMarketDetailMap:
    is_demo = config.ig_broker_is_demo()
    broker = IGBroker(is_demo=is_demo)
    if not broker.connect():
        env_name = "demo" if is_demo else "live"
        raise ValueError(f"Could not connect to IG {env_name} for market-rule preflight")

    details: IGMarketDetailMap = {}
    try:
        for ticker in sorted({str(item or "").strip() for item in proxy_tickers if str(item or "").strip()}):
            epic = broker.get_epic(ticker)
            if not epic:
                raise ValueError(f"No accessible IG EPIC for {ticker}")
            market_info = broker.get_market_info(epic)
            if not market_info:
                raise ValueError(f"Could not fetch IG market details for {ticker}")
            min_deal_size = _extract_min_deal_size(market_info)
            if min_deal_size <= 0:
                raise ValueError(f"IG market {ticker} did not return a usable min deal size")
            instrument = market_info.get("instrument", {}) if isinstance(market_info, dict) else {}
            snapshot = market_info.get("snapshot", {}) if isinstance(market_info, dict) else {}
            reference_price = _extract_snapshot_reference_price(market_info)
            details[ticker] = {
                "epic": epic,
                "min_deal_size": min_deal_size,
                "market_name": str(instrument.get("name") or ""),
                "market_status": str(snapshot.get("marketStatus") or ""),
                "expiry": str(instrument.get("expiry") or ""),
            }
            if reference_price is not None and reference_price > 0:
                details[ticker]["reference_price"] = float(reference_price)
    finally:
        broker.disconnect()

    return details


def build_manual_engine_a_trade_instruments(
    deltas: dict[str, float],
    *,
    size_mode: str = "auto",
    ig_market_details: IGMarketDetailMap | None = None,
    as_of: str | None = None,
) -> tuple[str, str, list[InstrumentSpec]]:
    if not deltas:
        raise ValueError("Rebalance has no executable deltas")

    broker_target = manual_engine_a_broker_target()
    resolved_size_mode = resolve_manual_engine_a_size_mode(
        broker_target=broker_target,
        size_mode=size_mode,
    )
    unsupported_instruments: list[str] = []
    instruments: list[InstrumentSpec] = []
    proxy_tickers = [
        ENGINE_A_IG_PROXY_TICKERS.get(instrument, "")
        for instrument in deltas
        if broker_target == "ig"
    ]
    market_details = (
        ig_market_details
        if ig_market_details is not None
        else (
            fetch_ig_market_details([ticker for ticker in proxy_tickers if ticker])
            if broker_target == "ig" and resolved_size_mode == "min"
            else {}
        )
    )
    paper_reference_prices = (
        fetch_engine_a_reference_prices(list(deltas), as_of=as_of)
        if broker_target == "paper"
        else {}
    )

    for instrument, delta in deltas.items():
        raw_order_qty = abs(float(delta))
        order_qty = raw_order_qty
        contract_details = [
            f"root_symbol={instrument}",
            f"delta_contracts={delta:.4f}",
            f"raw_order_qty={raw_order_qty:.4f}",
            f"route={broker_target}",
            f"size_mode={resolved_size_mode}",
        ]
        if broker_target == "paper":
            reference_price = float(paper_reference_prices.get(instrument, 0) or 0)
            if reference_price > 0:
                contract_details.append(f"reference_price={reference_price:.6f}")
            contract_details.append(f"order_qty={order_qty:.4f}")
            instruments.append(
                InstrumentSpec(
                    ticker=instrument,
                    instrument_type="future",
                    broker="paper",
                    contract_details=";".join(contract_details),
                )
            )
            continue

        proxy_ticker = ENGINE_A_IG_PROXY_TICKERS.get(instrument)
        if not proxy_ticker or proxy_ticker not in config.MARKET_MAP:
            unsupported_instruments.append(instrument)
            continue
        if resolved_size_mode == "min":
            market_detail = market_details.get(proxy_ticker)
            if not market_detail:
                raise ValueError(f"IG market details missing for {proxy_ticker}")
            order_qty = float(market_detail["min_deal_size"])
            contract_details.append(f"ig_min_deal_size={order_qty:.4f}")
            contract_details.append(f"ig_epic={market_detail['epic']}")
            if market_detail.get("market_status"):
                contract_details.append(f"market_status={market_detail['market_status']}")
            reference_price = float(market_detail.get("reference_price", 0) or 0)
            if reference_price > 0:
                contract_details.append(f"reference_price={reference_price:.6f}")
        contract_details.append(f"order_qty={order_qty:.4f}")
        contract_details.append(f"proxy_symbol={proxy_ticker}")
        instruments.append(
            InstrumentSpec(
                ticker=proxy_ticker,
                instrument_type="spread_bet",
                broker="ig",
                contract_details=";".join(contract_details),
            )
        )

    if unsupported_instruments:
        joined = ", ".join(sorted(set(unsupported_instruments)))
        raise ValueError(f"Engine A instruments cannot route through IG demo/live: {joined}")

    return broker_target, resolved_size_mode, instruments


def resolve_manual_engine_a_rebalance(
    *,
    chain_id: str = "",
    artifact_store: ArtifactStore | None = None,
) -> tuple[ArtifactStore, ArtifactEnvelope, dict[str, float]]:
    store = artifact_store or ArtifactStore()
    rebalance = (
        find_chain_artifact(chain_id, ArtifactType.REBALANCE_SHEET, artifact_store=store)
        if str(chain_id or "").strip()
        else latest_artifact_by_type(ArtifactType.REBALANCE_SHEET, engine=Engine.ENGINE_A, artifact_store=store)
    )
    if rebalance is None or not rebalance.chain_id:
        raise ValueError("No Engine A rebalance proposal is available to execute.")

    chain = store.get_chain(rebalance.chain_id)
    if any(
        envelope.artifact_type == ArtifactType.EXECUTION_REPORT and int(envelope.version or 0) > int(rebalance.version or 0)
        for envelope in chain
    ):
        raise ValueError(f"Latest Engine A rebalance for chain {rebalance.chain_id[:8]} has already been executed.")

    deltas = {
        instrument: float(delta)
        for instrument, delta in dict(rebalance.body).get("deltas", {}).items()
        if abs(float(delta or 0.0)) > 0.0
    }
    if not deltas:
        raise ValueError(f"Rebalance chain {rebalance.chain_id[:8]} has no executable deltas")

    return store, rebalance, deltas


def preview_manual_engine_a_rebalance(
    *,
    chain_id: str = "",
    artifact_store: ArtifactStore | None = None,
    size_mode: str = "auto",
    ig_market_details: IGMarketDetailMap | None = None,
    symbols: list[str] | None = None,
) -> ManualEngineAExecutionPreview:
    _, rebalance, deltas = resolve_manual_engine_a_rebalance(
        chain_id=chain_id,
        artifact_store=artifact_store,
    )
    deltas = filter_manual_engine_a_deltas(deltas, symbols=symbols)
    broker_target, resolved_size_mode, instruments = build_manual_engine_a_trade_instruments(
        deltas,
        size_mode=size_mode,
        ig_market_details=ig_market_details,
        as_of=dict(rebalance.body).get("as_of"),
    )
    return ManualEngineAExecutionPreview(
        chain_id=str(rebalance.chain_id or ""),
        rebalance=rebalance,
        deltas=deltas,
        broker_target=broker_target,
        size_mode=resolved_size_mode,
        instruments=instruments,
    )


def build_manual_engine_a_trade_sheet(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    actor: str,
    artifact_store: ArtifactStore,
    size_mode: str = "auto",
    ig_market_details: IGMarketDetailMap | None = None,
    symbols: list[str] | None = None,
) -> ArtifactEnvelope:
    regime_artifact = find_chain_artifact(chain_id, ArtifactType.REGIME_SNAPSHOT, artifact_store=artifact_store)
    signal_artifact = find_chain_artifact(chain_id, ArtifactType.ENGINE_A_SIGNAL_SET, artifact_store=artifact_store)
    deltas = {
        instrument: float(delta)
        for instrument, delta in dict(rebalance.body).get("deltas", {}).items()
        if abs(float(delta or 0.0)) > 0.0
    }
    deltas = filter_manual_engine_a_deltas(deltas, symbols=symbols)
    if not deltas:
        raise ValueError(f"Rebalance chain {chain_id[:8]} has no executable deltas")

    broker_target, resolved_size_mode, instruments = build_manual_engine_a_trade_instruments(
        deltas,
        size_mode=size_mode,
        ig_market_details=ig_market_details,
        as_of=dict(rebalance.body).get("as_of"),
    )
    trade_sheet = TradeSheet(
        hypothesis_ref=str((regime_artifact.artifact_id if regime_artifact else rebalance.artifact_id) or ""),
        experiment_ref=str((signal_artifact.artifact_id if signal_artifact else rebalance.artifact_id) or ""),
        instruments=instruments,
        sizing=SizingSpec(
            method="risk_parity",
            target_risk_pct=0.12,
            max_notional=sum(abs(delta) for delta in deltas.values()),
            sizing_parameters={
                "generated_at": dict(rebalance.body).get("as_of") or utc_now_iso(),
                "decision_source": "operator_execute",
                "broker_target": broker_target,
                "size_mode": resolved_size_mode,
            },
        ),
        entry_rules=["Submit manual Engine A rebalance approved from control plane."],
        exit_rules=["Exit or resize on next Engine A rebalance decision."],
        holding_period_target="daily_review",
        risk_limits=ResearchRiskLimits(
            max_loss_pct=5.0,
            max_portfolio_impact_pct=20.0,
            max_correlated_exposure_pct=40.0,
        ),
        kill_criteria=["regime_change", "drawdown", "cost_exceeded"],
    )
    envelope = ArtifactEnvelope(
        artifact_type=ArtifactType.TRADE_SHEET,
        engine=Engine.ENGINE_A,
        ticker=rebalance.ticker,
        edge_family=rebalance.edge_family,
        chain_id=chain_id,
        body=trade_sheet,
        created_by=operator_created_by(actor),
        tags=["engine_a", "trade_sheet", "manual_execute"],
    )
    envelope.artifact_id = artifact_store.save(envelope)
    return envelope


def queue_manual_engine_a_order_intents(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    trade_sheet: ArtifactEnvelope,
    actor: str,
    order_intent_creator: OrderIntentCreator = create_order_intent_envelope,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    trade_sheet_body = TradeSheet.model_validate(trade_sheet.body)
    rebalance_body = dict(rebalance.body)
    submitted: list[dict[str, Any]] = []

    for index, instrument in enumerate(trade_sheet_body.instruments):
        details = parse_contract_details(instrument.contract_details)
        delta = float(details.get("delta_contracts") or 0.0)
        qty = abs(float(details.get("order_qty") or 0.0))
        if qty <= 0.0:
            raise ValueError(f"TradeSheet instrument {instrument.ticker} is missing executable quantity")

        broker_target = str(instrument.broker).strip().lower()
        if broker_target not in {"paper", "ig"}:
            raise ValueError(f"Engine A research execution does not support broker '{instrument.broker}'")

        account_type = RouteAccountType.PAPER.value if broker_target == "paper" else RouteAccountType.SPREADBET.value
        intent = OrderIntent(
            strategy_id="research_engine_a_rebalance",
            strategy_version=f"artifact_v{int(rebalance.version or 1)}",
            sleeve="research_engine_a",
            account_type=account_type,
            broker_target=broker_target,
            instrument=instrument.ticker,
            side=OrderSide.BUY.value if delta > 0 else OrderSide.SELL.value,
            qty=qty,
            order_type="MARKET",
            risk_tags=["research", "engine_a", "rebalance", f"broker:{broker_target}"],
            metadata={
                "source": "research_execute_rebalance",
                "research_engine": "engine_a",
                "chain_id": chain_id,
                "rebalance_artifact_id": str(rebalance.artifact_id or ""),
                "trade_sheet_artifact_id": str(trade_sheet.artifact_id or ""),
                "root_symbol": details.get("root_symbol") or instrument.ticker,
                "proxy_symbol": details.get("proxy_symbol") or instrument.ticker,
                "delta_contracts": delta,
                "operator_id": str(actor or "").strip() or "operator",
                "signal_timestamp": rebalance_body.get("as_of") or utc_now_iso(),
                "is_exit": False,
            },
        )
        reference_price = float(details.get("reference_price") or 0.0)
        if reference_price > 0:
            intent.metadata["reference_price"] = reference_price
        submitted.append(
            order_intent_creator(
                intent=intent,
                action_type="research_rebalance",
                max_attempts=3,
                actor="operator",
                correlation_id=f"research_rebalance:{chain_id}:{rebalance.artifact_id}:{index}",
                request_payload={
                    "source": "research_execute_rebalance",
                    "chain_id": chain_id,
                    "rebalance_artifact_id": str(rebalance.artifact_id or ""),
                    "trade_sheet_artifact_id": str(trade_sheet.artifact_id or ""),
                    "operator_id": str(actor or "").strip() or "operator",
                },
                db_path=db_path,
            )
        )

    return submitted


def build_manual_engine_a_execution_report(
    *,
    chain_id: str,
    rebalance: ArtifactEnvelope,
    actor: str,
    artifact_store: ArtifactStore,
    queued_intents: list[dict[str, Any]],
) -> ArtifactEnvelope:
    rebalance_body = dict(rebalance.body)
    venue = str((queued_intents[0] if queued_intents else {}).get("broker_target") or "unknown").strip() or "unknown"
    report = ExecutionReport(
        as_of=rebalance_body.get("as_of") or utc_now_iso(),
        trades_submitted=len(queued_intents),
        trades_filled=0,
        fills=[],
        slippage=0.0,
        cost=float(rebalance_body.get("estimated_cost") or 0.0),
        venue=f"QUEUED:{venue}",
        latency=0.0,
    )
    envelope = ArtifactEnvelope(
        artifact_type=ArtifactType.EXECUTION_REPORT,
        engine=Engine.ENGINE_A,
        ticker=rebalance.ticker,
        edge_family=rebalance.edge_family,
        chain_id=chain_id,
        body=report,
        created_by=operator_created_by(actor),
        tags=["engine_a", "execution", "manual_execute"],
    )
    envelope.artifact_id = artifact_store.save(envelope)
    return envelope


def execute_manual_engine_a_rebalance(
    *,
    chain_id: str = "",
    actor: str = "operator",
    notes: str = "Operator approved and executed Engine A rebalance.",
    artifact_store: ArtifactStore | None = None,
    order_intent_creator: OrderIntentCreator = create_order_intent_envelope,
    db_path: str = DB_PATH,
    size_mode: str = "auto",
    ig_market_details: IGMarketDetailMap | None = None,
    symbols: list[str] | None = None,
) -> ManualEngineAExecutionResult:
    store, rebalance, deltas = resolve_manual_engine_a_rebalance(
        chain_id=chain_id,
        artifact_store=artifact_store,
    )
    deltas = filter_manual_engine_a_deltas(deltas, symbols=symbols)
    broker_target, resolved_size_mode, instruments = build_manual_engine_a_trade_instruments(
        deltas,
        size_mode=size_mode,
        ig_market_details=ig_market_details,
    )
    preview = ManualEngineAExecutionPreview(
        chain_id=str(rebalance.chain_id or ""),
        rebalance=rebalance,
        deltas=deltas,
        broker_target=broker_target,
        size_mode=resolved_size_mode,
        instruments=instruments,
    )
    approved_rebalance = supersede_rebalance_sheet(
        rebalance=rebalance,
        approval_status="approved",
        actor=actor,
        notes=notes,
        artifact_store=store,
    )
    trade_sheet = build_manual_engine_a_trade_sheet(
        chain_id=preview.chain_id,
        rebalance=approved_rebalance,
        actor=actor,
        artifact_store=store,
        size_mode=resolved_size_mode,
        ig_market_details=ig_market_details,
        symbols=symbols,
    )
    queued_intents = queue_manual_engine_a_order_intents(
        chain_id=preview.chain_id,
        rebalance=approved_rebalance,
        trade_sheet=trade_sheet,
        actor=actor,
        order_intent_creator=order_intent_creator,
        db_path=db_path,
    )
    execution_report = build_manual_engine_a_execution_report(
        chain_id=preview.chain_id,
        rebalance=approved_rebalance,
        actor=actor,
        artifact_store=store,
        queued_intents=queued_intents,
    )
    return ManualEngineAExecutionResult(
        preview=preview,
        approved_rebalance=approved_rebalance,
        trade_sheet=trade_sheet,
        execution_report=execution_report,
        queued_intents=queued_intents,
    )
