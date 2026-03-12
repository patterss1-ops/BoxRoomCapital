"""Broker API routes and HTMX fragments."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config
from app.api import broker_helpers as _broker_helpers
from app.api import fragment_context_helpers as _fragment_context_helpers
import app.api.shared as _shared_mod
from app.api.shared import (
    TEMPLATES,
    _broker_lock,
    _BROKER_HEALTH_CACHE_TTL_SECONDS,
    _BROKER_SNAPSHOT_CACHE_TTL_SECONDS,
    _LEDGER_CACHE_TTL_SECONDS,
    _MARKET_BROWSER_CACHE_TTL_SECONDS,
    _UI_BROKER_MARKET_TIMEOUT_SECONDS,
    _UI_BROKER_TIMEOUT_SECONDS,
    action_message,
    control,
    _get_cached_value,
    _get_or_create_broker,
    _invalidate_cached_values,
)
from data.trade_db import (
    get_ledger_reconcile_report,
    get_option_contract_summary,
    get_option_contracts,
    get_order_actions,
    get_unified_ledger_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["broker"])


# ─── Broker-specific helpers ──────────────────────────────────────────────────


def _load_order_intent_store():
    return _broker_helpers._load_order_intent_store()


def _safe_json_load(raw: Any) -> Any:
    return _broker_helpers._safe_json_load(raw)


def _normalize_intent_item(row: dict[str, Any], source: str) -> dict[str, Any]:
    return _broker_helpers._normalize_intent_item(row, source)


def get_order_intent_items(limit: int = 50, status: str = "") -> list[dict[str, Any]]:
    return _broker_helpers.get_order_intent_items(
        limit=limit,
        status=status,
        load_order_intent_store=_load_order_intent_store,
        get_order_actions=get_order_actions,
    )


def get_order_intent_detail(intent_id: str) -> Optional[dict[str, Any]]:
    return _broker_helpers.get_order_intent_detail(
        intent_id,
        load_order_intent_store=_load_order_intent_store,
        get_order_actions=get_order_actions,
    )


def build_broker_health_payload() -> dict[str, Any]:
    return _broker_helpers.build_broker_health_payload(
        control_obj=control,
        shared_broker=_shared_mod._broker,
        asdict_fn=asdict,
        is_dataclass_fn=is_dataclass,
    )


def _get_broker_snapshot() -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        connected = _shared_mod._broker is not None and _shared_mod._broker.is_connected()
        info = None
        positions = []
        if connected and _shared_mod._broker is not None:
            info = _shared_mod._broker.get_account_info(timeout=_UI_BROKER_TIMEOUT_SECONDS)
            positions = _shared_mod._broker.get_positions(timeout=_UI_BROKER_TIMEOUT_SECONDS)
        return {
            "connected": connected,
            "info": info,
            "positions": positions,
        }

    return _get_cached_value(
        "broker-snapshot",
        _BROKER_SNAPSHOT_CACHE_TTL_SECONDS,
        _load,
        stale_on_error=True,
    )


def _get_market_browser_context() -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        connected = _shared_mod._broker is not None and _shared_mod._broker.is_connected()
        markets = []
        for ticker, info in config.MARKET_MAP.items():
            entry = {
                "ticker": ticker,
                "epic": info["epic"],
                "ig_name": info.get("ig_name", ticker),
                "status": None,
                "bid": None,
                "offer": None,
            }
            if connected and _shared_mod._broker is not None:
                mkt = _shared_mod._broker.get_market_info(
                    info["epic"],
                    timeout=_UI_BROKER_MARKET_TIMEOUT_SECONDS,
                )
                if mkt:
                    snap = mkt.get("snapshot", {})
                    entry["status"] = snap.get("marketStatus")
                    entry["bid"] = snap.get("bid")
                    entry["offer"] = snap.get("offer")
            markets.append(entry)
        return {"connected": connected, "markets": markets}

    return _get_cached_value(
        "market-browser",
        _MARKET_BROWSER_CACHE_TTL_SECONDS,
        _load,
        stale_on_error=True,
    )


def _get_ledger_fragment_context() -> dict[str, Any]:
    return _fragment_context_helpers._get_ledger_fragment_context(
        get_cached_value=_get_cached_value,
        ledger_cache_ttl_seconds=_LEDGER_CACHE_TTL_SECONDS,
        get_unified_ledger_snapshot=get_unified_ledger_snapshot,
        get_ledger_reconcile_report=get_ledger_reconcile_report,
    )


# ─── API routes ───────────────────────────────────────────────────────────────


@router.get("/api/broker-health")
def api_broker_health():
    return build_broker_health_payload()


@router.post("/api/broker/connect")
def api_broker_connect():
    with _broker_lock:
        _shared_mod._broker = None
    broker, err = _get_or_create_broker()
    if not broker:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _invalidate_cached_values("broker-snapshot", "broker-health")
    info = broker.get_account_info()
    return {
        "ok": True,
        "account": config.ig_account_number(broker.is_demo),
        "mode": "DEMO" if broker.is_demo else "LIVE",
        "balance": info.balance,
        "equity": info.equity,
        "unrealised_pnl": info.unrealised_pnl,
        "currency": info.currency,
    }


@router.get("/api/broker/status")
def api_broker_status():
    if not _shared_mod._broker or not _shared_mod._broker.is_connected():
        return {"connected": False, "message": "Not connected. POST /api/broker/connect first."}
    info = _shared_mod._broker.get_account_info()
    positions = _shared_mod._broker.get_positions()
    return {
        "connected": True,
        "account": config.ig_account_number(_shared_mod._broker.is_demo),
        "mode": "DEMO" if _shared_mod._broker.is_demo else "LIVE",
        "balance": info.balance,
        "equity": info.equity,
        "unrealised_pnl": info.unrealised_pnl,
        "currency": info.currency,
        "open_positions": len(positions),
    }


@router.get("/api/broker/positions")
def api_broker_positions():
    if not _shared_mod._broker or not _shared_mod._broker.is_connected():
        return JSONResponse({"error": "Not connected"}, status_code=400)
    positions = _shared_mod._broker.get_positions()
    return {
        "count": len(positions),
        "positions": [
            {
                "deal_id": p.deal_id,
                "ticker": p.ticker,
                "direction": p.direction,
                "size": p.size,
                "entry_price": p.entry_price,
                "unrealised_pnl": p.unrealised_pnl,
                "strategy": p.strategy,
            }
            for p in positions
        ],
    }


@router.get("/api/broker/market/{epic:path}")
def api_broker_market(epic: str):
    if not _shared_mod._broker or not _shared_mod._broker.is_connected():
        return JSONResponse({"error": "Not connected"}, status_code=400)
    info = _shared_mod._broker.get_market_info(epic)
    if not info:
        return JSONResponse({"error": f"Market {epic} not found or blocked"}, status_code=404)
    snap = info.get("snapshot", {})
    inst = info.get("instrument", {})
    rules = info.get("dealingRules", {})
    return {
        "epic": epic,
        "name": inst.get("name"),
        "status": snap.get("marketStatus"),
        "bid": snap.get("bid"),
        "offer": snap.get("offer"),
        "high": snap.get("high"),
        "low": snap.get("low"),
        "min_deal_size": rules.get("minDealSize", {}).get("value"),
        "min_stop_distance": rules.get("minNormalStopOrLimitDistance", {}).get("value"),
        "expiry": inst.get("expiry"),
    }


@router.get("/api/broker/markets")
def api_broker_markets():
    connected = _shared_mod._broker is not None and _shared_mod._broker.is_connected()
    markets = []
    for ticker, info in config.MARKET_MAP.items():
        entry = {
            "ticker": ticker,
            "epic": info["epic"],
            "ig_name": info.get("ig_name", ""),
            "strategy": info.get("strategy", ""),
            "verified": info.get("verified", False),
        }
        if connected:
            mkt = _shared_mod._broker.get_market_info(info["epic"])
            if mkt:
                snap = mkt.get("snapshot", {})
                entry["status"] = snap.get("marketStatus")
                entry["bid"] = snap.get("bid")
                entry["offer"] = snap.get("offer")
                entry["live"] = True
            else:
                entry["live"] = False
        markets.append(entry)
    return {"connected": connected, "markets": markets}


@router.post("/api/broker/open-position")
async def api_broker_open_position(request: Request):
    if not _shared_mod._broker or not _shared_mod._broker.is_connected():
        return JSONResponse({"error": "Not connected"}, status_code=400)

    body = await request.json()
    epic = body.get("epic", "")
    direction = body.get("direction", "BUY").upper()
    size = float(body.get("size", 0))

    if not epic or size <= 0:
        return JSONResponse({"error": "epic, direction, size required"}, status_code=400)
    if direction not in ("BUY", "SELL"):
        return JSONResponse({"error": "direction must be BUY or SELL"}, status_code=400)

    # Resolve ticker from epic (reverse lookup), or use epic as ticker
    ticker = epic
    for t, info in config.MARKET_MAP.items():
        if info["epic"] == epic:
            ticker = t
            break

    if ticker != epic:
        # Use place_long/place_short which handle stop distances etc.
        if direction == "BUY":
            result = _shared_mod._broker.place_long(ticker, size, "api_manual")
        else:
            result = _shared_mod._broker.place_short(ticker, size, "api_manual")
    else:
        result = _shared_mod._broker._place_option_leg(epic, direction, size, epic, "api_manual")

    return {
        "ok": result.success,
        "deal_id": result.order_id,
        "fill_price": result.fill_price,
        "fill_qty": result.fill_qty,
        "message": result.message,
    }


@router.post("/api/broker/close-position")
async def api_broker_close_position(request: Request):
    if not _shared_mod._broker or not _shared_mod._broker.is_connected():
        return JSONResponse({"error": "Not connected"}, status_code=400)

    body = await request.json()
    deal_id = body.get("deal_id", "")

    if not deal_id:
        return JSONResponse({"error": "deal_id required"}, status_code=400)

    # Find the position to get direction and size
    positions = _shared_mod._broker.get_positions()
    target = None
    for p in positions:
        if p.deal_id == deal_id:
            target = p
            break

    if not target:
        return JSONResponse({"error": f"No open position with deal_id={deal_id}"}, status_code=404)

    close_direction = "SELL" if target.direction == "long" else "BUY"
    close_payload = {
        "dealId": deal_id,
        "direction": close_direction,
        "size": str(target.size),
        "orderType": "MARKET",
    }

    r = _shared_mod._broker.session.post(
        f"{_shared_mod._broker.base_url}/positions/otc",
        json=close_payload,
        headers={**_shared_mod._broker._headers("1"), "_method": "DELETE"},
    )

    if r.status_code != 200:
        return JSONResponse({"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}, status_code=502)

    import time
    close_ref = r.json().get("dealReference", "")
    if not close_ref:
        return JSONResponse({"ok": False, "error": "No deal reference returned"}, status_code=502)

    time.sleep(1)
    result = _shared_mod._broker._confirm_deal(close_ref, target.ticker, target.strategy, target.size)
    return {
        "ok": result.success,
        "deal_id": result.order_id,
        "fill_price": result.fill_price,
        "message": result.message,
    }


@router.get("/api/order-actions")
def api_order_actions(limit: int = 50, status: str = ""):
    return {"items": get_order_actions(limit=limit, status=status or None)}


@router.get("/api/order-intents")
def api_order_intents(limit: int = 50, status: str = ""):
    return {"items": get_order_intent_items(limit=limit, status=status)}


@router.get("/api/order-intents/{intent_id}")
def api_order_intent_detail(intent_id: str):
    detail = get_order_intent_detail(intent_id)
    if not detail:
        return JSONResponse({"error": "intent_not_found"}, status_code=404)
    return {"item": detail}


@router.get("/api/reconcile-report")
def api_reconcile_report():
    return control.reconcile_report()


@router.get("/api/options/contracts")
def api_option_contracts(limit: int = 200, index_name: str = "", expiry_type: str = ""):
    return {
        "items": get_option_contracts(
            limit=limit,
            index_name=index_name or None,
            expiry_type=expiry_type or None,
        )
    }


@router.get("/api/options/summary")
def api_option_summary():
    return {"items": get_option_contract_summary()}


@router.post("/api/actions/manual-trade", response_class=HTMLResponse)
def manual_trade_action(
    epic: str = Form(default=""),
    direction: str = Form(default="BUY"),
    size: float = Form(default=0.5),
):
    if not epic:
        return action_message("EPIC is required.", ok=False)
    if direction not in ("BUY", "SELL"):
        return action_message("Direction must be BUY or SELL.", ok=False)
    if size <= 0:
        return action_message("Size must be positive.", ok=False)

    broker, err = _get_or_create_broker()
    if not broker:
        return action_message(f"Broker not available: {err}", ok=False)

    # Reverse-lookup ticker from EPIC
    ticker = epic
    for t, info in config.MARKET_MAP.items():
        if info["epic"] == epic:
            ticker = t
            break

    if direction == "BUY":
        result = broker.place_long(ticker, size, "manual_trade")
    else:
        result = broker.place_short(ticker, size, "manual_trade")

    if result.success:
        return action_message(
            f"{direction} {ticker} @ {result.fill_price} — deal {result.order_id or 'confirmed'}",
            ok=True,
        )
    return action_message(f"Trade failed: {result.message}", ok=False)


@router.post("/api/actions/close-deal", response_class=HTMLResponse)
def close_deal_action(deal_id: str = Form(default="")):
    if not deal_id:
        return action_message("deal_id is required.", ok=False)

    broker, err = _get_or_create_broker()
    if not broker:
        return action_message(f"Broker not available: {err}", ok=False)

    positions = broker.get_positions()
    target = None
    for p in positions:
        if p.deal_id == deal_id:
            target = p
            break
    if not target:
        return action_message(f"No open position with deal_id={deal_id}", ok=False)

    close_direction = "SELL" if target.direction == "long" else "BUY"
    result = broker._close_option_leg(deal_id, close_direction, target.size)
    if result.success:
        return action_message(
            f"Closed {target.ticker} {target.direction} @ {result.fill_price}",
            ok=True,
        )
    return action_message(f"Close failed: {result.message}", ok=False)


@router.get("/api/charts/market-prices")
def api_market_prices(epic: str = "", resolution: str = "HOUR", points: int = 48):
    """Fetch price history from IG for lightweight-charts."""
    _VALID_RESOLUTIONS = {"MINUTE", "MINUTE_5", "MINUTE_15", "MINUTE_30", "HOUR", "HOUR_4", "DAY", "WEEK"}
    if not epic or resolution not in _VALID_RESOLUTIONS:
        return []
    points = max(1, min(points, 200))

    broker, err = _get_or_create_broker()
    if not broker:
        return []

    try:
        r = broker.session.get(
            f"{broker.base_url}/prices/{epic}",
            params={"resolution": resolution, "max": points, "pageSize": points},
            headers=broker._headers("3"),
            timeout=broker._TIMEOUT,
        )
        if r.status_code != 200:
            return []

        data = r.json()
        result = []
        for candle in data.get("prices", []):
            snap_time = candle.get("snapshotTime", "")
            close_price = candle.get("closePrice", {})
            mid = close_price.get("bid")
            if mid is None:
                continue
            # IG returns snapshotTime as "2026/03/04 14:00:00"
            # lightweight-charts needs UTC epoch seconds
            try:
                dt = datetime.strptime(snap_time, "%Y/%m/%d %H:%M:%S")
                epoch = int(dt.replace(tzinfo=timezone.utc).timestamp())
            except (ValueError, TypeError):
                continue
            result.append({"time": epoch, "value": float(mid)})
        return result
    except Exception:
        return []


@router.get("/api/charts/ohlcv")
def api_chart_ohlcv(ticker: str = "SPY", period: str = "6mo", interval: str = "1d"):
    """Fetch OHLCV candlestick data via yfinance for rich charting."""
    import yfinance as yf
    _VALID_PERIODS = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"}
    _VALID_INTERVALS = {"1m", "5m", "15m", "1h", "1d", "1wk"}
    if not ticker:
        return {"candles": [], "volumes": [], "ticker": ticker}
    if period not in _VALID_PERIODS:
        period = "6mo"
    if interval not in _VALID_INTERVALS:
        interval = "1d"
    # Short intervals require short periods
    if interval in {"1m", "5m", "15m"} and period not in {"5d"}:
        period = "5d"
    elif interval == "1h" and period not in {"5d", "1mo", "3mo"}:
        period = "3mo"

    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df is None or df.empty:
            return {"candles": [], "volumes": [], "ticker": ticker}
        # Flatten MultiIndex columns if present
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = df.columns.get_level_values(0)
        candles = []
        volumes = []
        for idx, row in df.iterrows():
            try:
                if hasattr(idx, "timestamp"):
                    epoch = int(idx.timestamp())
                else:
                    epoch = int(datetime.combine(idx, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp())
                o = float(row["Open"])
                h = float(row["High"])
                l = float(row["Low"])
                c = float(row["Close"])
                v = int(row["Volume"]) if row["Volume"] == row["Volume"] else 0
                candles.append({"time": epoch, "open": round(o, 4), "high": round(h, 4), "low": round(l, 4), "close": round(c, 4)})
                color = "rgba(38,166,154,0.5)" if c >= o else "rgba(239,83,80,0.5)"
                volumes.append({"time": epoch, "value": v, "color": color})
            except (ValueError, TypeError, KeyError):
                continue
        return {"candles": candles, "volumes": volumes, "ticker": ticker}
    except Exception as exc:
        logger.warning("Chart OHLCV fetch failed for %s: %s", ticker, exc)
        return {"candles": [], "volumes": [], "ticker": ticker, "error": str(exc)}


# ─── Fragment routes (broker UI) ──────────────────────────────────────────────


@router.get("/fragments/broker-health", response_class=HTMLResponse)
def broker_health_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_broker_health.html",
        {
            "request": request,
            "broker_health": _get_cached_value(
                "broker-health",
                _BROKER_HEALTH_CACHE_TTL_SECONDS,
                build_broker_health_payload,
                stale_on_error=True,
            ),
        },
    )


@router.get("/fragments/broker-panel", response_class=HTMLResponse)
def broker_panel_fragment(request: Request):
    snapshot = _get_broker_snapshot()
    connected = bool(snapshot.get("connected"))
    ctx: dict[str, Any] = {"request": request, "connected": connected}
    if connected:
        info = snapshot.get("info")
        positions = snapshot.get("positions", [])
        ctx["account"] = config.ig_account_number(_shared_mod._broker.is_demo)
        ctx["mode"] = "DEMO" if _shared_mod._broker.is_demo else "LIVE"
        ctx["balance"] = info.balance
        ctx["equity"] = info.equity
        ctx["unrealised_pnl"] = info.unrealised_pnl
        ctx["currency"] = info.currency
        ctx["open_positions"] = len(positions)
    return TEMPLATES.TemplateResponse(request, "_broker_panel.html", ctx)


@router.get("/fragments/market-browser", response_class=HTMLResponse)
def market_browser_fragment(request: Request):
    context = _get_market_browser_context()
    return TEMPLATES.TemplateResponse(
        request,
        "_market_browser.html",
        {
            "request": request,
            "connected": context["connected"],
            "markets": context["markets"],
        },
    )


@router.get("/fragments/open-positions", response_class=HTMLResponse)
def open_positions_fragment(request: Request):
    snapshot = _get_broker_snapshot()
    connected = bool(snapshot.get("connected"))
    positions = []
    if connected:
        for p in snapshot.get("positions", []):
            positions.append({
                "deal_id": p.deal_id,
                "ticker": p.ticker,
                "direction": p.direction,
                "size": p.size,
                "entry_price": p.entry_price,
                "unrealised_pnl": p.unrealised_pnl,
            })
    return TEMPLATES.TemplateResponse(
        request,
        "_open_positions.html",
        {"request": request, "connected": connected, "positions": positions},
    )


@router.get("/fragments/order-actions", response_class=HTMLResponse)
def order_actions_fragment(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "_order_actions.html",
        {"request": request, "order_actions": get_order_actions(limit=25)},
    )


@router.get("/fragments/intent-audit", response_class=HTMLResponse)
def intent_audit_fragment(request: Request, intent_id: str = ""):
    intents = get_order_intent_items(limit=20, status="")
    selected_id = intent_id.strip() or (intents[0]["intent_id"] if intents else "")
    selected_detail = get_order_intent_detail(selected_id) if selected_id else None
    return TEMPLATES.TemplateResponse(
        request,
        "_intent_audit.html",
        {
            "request": request,
            "intents": intents,
            "selected_intent_id": selected_id,
            "selected_detail": selected_detail,
        },
    )


@router.get("/fragments/reconcile-report", response_class=HTMLResponse)
def reconcile_report_fragment(request: Request):
    report = control.reconcile_report().get("report", {})
    return TEMPLATES.TemplateResponse(
        request,
        "_reconcile_report.html",
        {"request": request, "report": report},
    )


@router.get("/fragments/ledger", response_class=HTMLResponse)
def ledger_fragment(request: Request):
    context = _get_ledger_fragment_context()
    return TEMPLATES.TemplateResponse(
        request,
        "_ledger_snapshot.html",
        {
            "request": request,
            "ledger": context["ledger"],
            "reconcile": context["reconcile"],
        },
    )
