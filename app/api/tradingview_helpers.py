"""Shared TradingView webhook helper functions."""
from __future__ import annotations

import json
from typing import Any, Callable


def _tradingview_event_descriptor(alert: Any) -> dict[str, Any]:
    return {
        "provider": "tradingview",
        "strategy_id": alert.strategy_id,
        "ticker": alert.ticker,
        "action": alert.action,
        "timeframe": alert.timeframe,
        "alert_id": alert.alert_id,
        "event_timestamp": alert.event_timestamp,
    }


def _tradingview_event_id(
    alert: Any,
    *,
    compute_event_id: Callable[..., str],
) -> str:
    return compute_event_id(
        event_type="signal",
        source="tradingview",
        descriptor=_tradingview_event_descriptor(alert),
        source_ref=alert.source_ref,
    )


def _decode_json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_tradingview_lane(
    strategy_id: str,
    db_path: str,
    *,
    get_active_strategy_parameter_set: Callable[..., dict[str, Any] | None],
) -> tuple[str, dict[str, Any] | None]:
    for lane in ("live", "staged_live", "shadow"):
        active = get_active_strategy_parameter_set(strategy_id, status=lane, db_path=db_path)
        if active:
            return lane, active
    return "missing", None


def _tradingview_action_semantics(
    action: str,
    *,
    order_side_enum: Any,
) -> tuple[str, bool]:
    clean = str(action or "").strip().lower()
    mapping = {
        "buy": (order_side_enum.BUY.value, False),
        "sell": (order_side_enum.SELL.value, True),
        "short": (order_side_enum.SELL.value, False),
        "cover": (order_side_enum.BUY.value, True),
    }
    result = mapping.get(clean)
    if result is None:
        raise ValueError(f"Unsupported TradingView action '{action}'")
    return result


def _build_tradingview_route_state(
    engine_status: dict[str, Any],
    *,
    route_policy_state_cls: Any,
) -> Any:
    cooldowns = engine_status.get("cooldowns") or {}
    cooldown_tickers = {
        str(ticker).upper()
        for ticker in cooldowns.keys()
        if str(ticker).strip()
    }
    return route_policy_state_cls(
        kill_switch_active=bool(engine_status.get("kill_switch_active")),
        kill_switch_reason=str(engine_status.get("kill_switch_reason") or ""),
        cooldown_tickers=cooldown_tickers,
    )


def _build_tradingview_router(
    spec: Any,
    *,
    account_router_cls: Any,
    route_config_entry_cls: Any,
    route_account_type_cls: Any,
    default_broker_resolver: Callable[[str], Any],
) -> Any:
    broker_name = str(spec.broker_target or "").strip().lower()
    return account_router_cls(
        route_map={
            f"strategy:{spec.strategy_id}": route_config_entry_cls(
                broker_name=broker_name,
                account_type=route_account_type_cls(spec.account_type),
            ),
        },
        brokers={broker_name: default_broker_resolver(broker_name)},
    )


def _get_tradingview_equity(
    db_path: str,
    *,
    get_conn: Callable[[str], Any],
) -> float:
    try:
        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT total_nav FROM fund_daily_report ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return 0.0
    if not row:
        return 0.0
    try:
        return float(row["total_nav"] or 0.0)
    except (TypeError, ValueError, KeyError):
        return 0.0


def _build_tradingview_risk_context(
    engine_status: dict[str, Any],
    db_path: str,
    *,
    get_tradingview_equity: Callable[[str], float],
    get_conn: Callable[[str], Any],
    risk_context_cls: Any,
) -> Any | None:
    equity = get_tradingview_equity(db_path)
    if equity <= 0:
        return None

    conn = get_conn(db_path)
    ticker_rows = conn.execute(
        """SELECT UPPER(bp.ticker) as ticker,
                  SUM(ABS(CAST(bp.market_value AS REAL))) as exposure
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1
           GROUP BY UPPER(bp.ticker)"""
    ).fetchall()
    sleeve_rows = conn.execute(
        """SELECT COALESCE(bp.sleeve, 'unassigned') as sleeve,
                  SUM(ABS(CAST(bp.market_value AS REAL))) as exposure
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1
           GROUP BY COALESCE(bp.sleeve, 'unassigned')"""
    ).fetchall()
    conn.close()

    cooldowns = engine_status.get("cooldowns") or {}
    return risk_context_cls(
        equity=equity,
        kill_switch_active=bool(engine_status.get("kill_switch_active")),
        kill_switch_reason=str(engine_status.get("kill_switch_reason") or ""),
        cooldown_tickers={
            str(ticker).upper()
            for ticker in cooldowns.keys()
            if str(ticker).strip()
        },
        ticker_exposure_notional={
            str(row["ticker"]).upper(): float(row["exposure"] or 0.0)
            for row in ticker_rows
        },
        sleeve_exposure_notional={
            str(row["sleeve"]): float(row["exposure"] or 0.0)
            for row in sleeve_rows
        },
    )


def _estimate_tradingview_notional(alert: Any, spec: Any) -> float:
    if alert.signal_price and alert.signal_price > 0:
        return float(alert.signal_price) * float(spec.base_qty)
    return float(spec.base_qty)


def _build_tradingview_event_record(
    alert: Any,
    lane: str,
    client_ip: str,
    state: str,
    *,
    event_record_cls: Any,
    utc_now_iso: Callable[[], str],
    compute_event_id: Callable[..., str],
    intent_id: str = "",
    rejection_code: str = "",
    rejection_detail: str = "",
    duplicate_count: int = 0,
) -> Any:
    payload = {
        "schema_version": alert.schema_version,
        "alert_id": alert.alert_id,
        "strategy_id": alert.strategy_id,
        "ticker": alert.ticker,
        "action": alert.action,
        "timeframe": alert.timeframe,
        "event_timestamp": alert.event_timestamp,
        "signal_price": alert.signal_price,
        "indicators": dict(alert.indicators),
        "state": state,
        "lane": lane,
        "intent_id": intent_id,
        "rejection_code": rejection_code,
        "rejection_detail": rejection_detail,
        "client_ip": client_ip,
        "correlation_id": alert.correlation_id,
        "duplicate_count": max(0, int(duplicate_count)),
        "raw_payload": dict(alert.raw_payload),
    }
    detail = {
        "state": state,
        "lane": lane,
        "client_ip": client_ip,
        "alert_id": alert.alert_id,
        "strategy_id": alert.strategy_id,
        "ticker": alert.ticker,
        "action": alert.action,
        "rejection_code": rejection_code,
    }
    return event_record_cls(
        event_type="signal",
        source="tradingview",
        source_ref=alert.source_ref,
        retrieved_at=utc_now_iso(),
        event_timestamp=alert.event_timestamp,
        symbol=alert.ticker,
        headline=f"TradingView alert {state}: {alert.action} {alert.ticker}",
        detail=json.dumps(detail, sort_keys=True),
        confidence=1.0,
        provenance_descriptor=_tradingview_event_descriptor(alert),
        payload=payload,
        event_id=_tradingview_event_id(alert, compute_event_id=compute_event_id),
    )
