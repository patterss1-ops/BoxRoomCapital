"""Shared broker status and order-intent helpers."""
from __future__ import annotations

import json
from typing import Any, Callable, Optional


def _load_order_intent_store():
    try:
        from data import order_intent_store

        return order_intent_store
    except Exception:
        return None


def _safe_json_load(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _normalize_intent_item(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "intent_id": row.get("intent_id") or row.get("id") or "",
        "correlation_id": row.get("correlation_id") or "",
        "status": str(row.get("status") or "queued").lower(),
        "strategy_id": row.get("strategy_id") or row.get("action_type") or "-",
        "strategy_version": row.get("strategy_version") or "-",
        "sleeve": row.get("sleeve") or "-",
        "account_type": row.get("account_type") or "-",
        "broker_target": row.get("broker_target") or "ig",
        "instrument": row.get("instrument") or row.get("ticker") or "-",
        "updated_at": row.get("updated_at") or row.get("created_at") or "-",
        "source": source,
    }


def get_order_intent_items(
    limit: int = 50,
    status: str = "",
    *,
    load_order_intent_store: Callable[[], Any] = _load_order_intent_store,
    get_order_actions: Callable[..., list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    store = load_order_intent_store()
    if store:
        try:
            rows = store.get_order_intents(limit=limit, status=status or None)
            return [_normalize_intent_item(dict(row), source="order_intents") for row in rows]
        except Exception:
            pass

    rows = get_order_actions(limit=limit, status=status or None)
    return [_normalize_intent_item(dict(row), source="order_actions_fallback") for row in rows]


def get_order_intent_detail(
    intent_id: str,
    *,
    load_order_intent_store: Callable[[], Any] = _load_order_intent_store,
    get_order_actions: Callable[..., list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    clean_id = intent_id.strip()
    if not clean_id:
        return None

    store = load_order_intent_store()
    if store:
        try:
            item = store.get_order_intent(clean_id)
            if item:
                attempts = store.get_order_intent_attempts(clean_id)
                transitions = store.get_order_intent_transitions(clean_id)
                return {
                    "source": "order_intents",
                    "intent": _normalize_intent_item(dict(item), source="order_intents"),
                    "attempts": attempts,
                    "transitions": transitions,
                }
        except Exception:
            pass

    for row in get_order_actions(limit=500):
        if row.get("id") == clean_id or row.get("correlation_id") == clean_id:
            normalized = _normalize_intent_item(dict(row), source="order_actions_fallback")
            attempt = int(row.get("attempt", 0) or 0)
            transition = {
                "from_status": "running" if normalized["status"] in {"completed", "failed", "retrying"} else None,
                "to_status": normalized["status"],
                "attempt": attempt,
                "transition_at": row.get("updated_at") or row.get("created_at"),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
                "response_payload": _safe_json_load(row.get("result_payload")),
            }
            attempt_row = {
                "attempt": attempt,
                "status": normalized["status"],
                "updated_at": row.get("updated_at"),
                "request_payload": _safe_json_load(row.get("request_payload")),
                "response_payload": _safe_json_load(row.get("result_payload")),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
            }
            return {
                "source": "order_actions_fallback",
                "intent": normalized,
                "attempts": [attempt_row],
                "transitions": [transition],
            }
    return None


def build_broker_health_payload(
    *,
    control_obj: Any,
    shared_broker: Any,
    asdict_fn: Callable[[Any], dict[str, Any]],
    is_dataclass_fn: Callable[[Any], bool],
) -> dict[str, Any]:
    engine_status = control_obj.status()
    payload: dict[str, Any] = {
        "broker": "unknown",
        "broker_class": "-",
        "engine_running": bool(engine_status.get("running")),
        "engine_mode": engine_status.get("mode"),
        "kill_switch_active": bool(engine_status.get("kill_switch_active")),
        "connected": False,
        "account": "",
        "host": "",
        "port": "",
        "server_time": None,
        "error": "",
        "message": "",
        "capabilities": {},
        "ready": False,
    }

    engine = getattr(control_obj, "engine", None)
    bot = getattr(engine, "_bot", None) if engine else None
    broker = getattr(bot, "broker", None) if bot else None

    if not broker and shared_broker is not None and shared_broker.is_connected():
        broker = shared_broker

    if not broker:
        payload["message"] = "No active broker session. POST /api/broker/connect to connect."
        return payload

    broker_class = broker.__class__.__name__
    payload["broker_class"] = broker_class
    payload["broker"] = broker_class.replace("Broker", "").lower() or "unknown"

    try:
        caps = broker.get_capabilities() if hasattr(broker, "get_capabilities") else {}
        if is_dataclass_fn(caps):
            payload["capabilities"] = asdict_fn(caps)
        elif isinstance(caps, dict):
            payload["capabilities"] = dict(caps)
    except Exception:
        payload["capabilities"] = {}

    health_data: dict[str, Any] = {}
    if hasattr(broker, "health_check"):
        try:
            health_data = broker.health_check() or {}
        except Exception as exc:
            payload["error"] = str(exc)
            health_data = {}

    if health_data:
        payload["connected"] = bool(health_data.get("connected", False))
        payload["account"] = str(health_data.get("account") or "")
        payload["host"] = str(health_data.get("host") or "")
        payload["port"] = str(health_data.get("port") or "")
        payload["server_time"] = health_data.get("server_time")
        if health_data.get("error"):
            payload["error"] = str(health_data.get("error"))
    else:
        if hasattr(broker, "is_connected"):
            try:
                payload["connected"] = bool(broker.is_connected())
            except Exception as exc:
                payload["error"] = str(exc)
        else:
            payload["connected"] = bool(engine_status.get("running"))

    payload["ready"] = bool(
        payload["connected"]
        and not payload["kill_switch_active"]
        and not payload["error"]
    )
    if not payload["message"]:
        if payload["ready"] and payload["engine_running"]:
            payload["message"] = "Broker lane ready."
        elif payload["ready"]:
            payload["message"] = "Broker connected (engine not running)."
        else:
            payload["message"] = "Broker lane degraded."

    return payload

