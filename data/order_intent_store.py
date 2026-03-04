"""Persistence helpers for unified order intents and audit envelopes."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)

from data.trade_db import DB_PATH, create_order_action, get_conn, update_order_action
from execution.order_intent import OrderIntent, OrderIntentStatus, normalize_actor, normalize_status
from utils.datetime_utils import parse_iso_utc


_TRANSITION_RULES: dict[OrderIntentStatus, set[OrderIntentStatus]] = {
    OrderIntentStatus.QUEUED: {
        OrderIntentStatus.RUNNING,
        OrderIntentStatus.FAILED,
        OrderIntentStatus.ABORTED,
    },
    OrderIntentStatus.RUNNING: {
        OrderIntentStatus.RETRYING,
        OrderIntentStatus.COMPLETED,
        OrderIntentStatus.FAILED,
        OrderIntentStatus.ABORTED,
    },
    OrderIntentStatus.RETRYING: {
        OrderIntentStatus.RUNNING,
        OrderIntentStatus.FAILED,
        OrderIntentStatus.ABORTED,
    },
    OrderIntentStatus.COMPLETED: set(),
    OrderIntentStatus.FAILED: set(),
    OrderIntentStatus.ABORTED: set(),
}


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _json_load(value: Optional[str]) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


_REFERENCE_PRICE_KEYS: tuple[str, ...] = (
    "reference_price",
    "signal_price",
    "expected_price",
    "decision_price",
    "last_close",
    "close_price",
)


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    return parse_iso_utc(value)


def _extract_reference_price(
    metadata: Any,
    response_payload: Any,
) -> Optional[float]:
    meta = metadata if isinstance(metadata, dict) else _json_load(_json_dumps(metadata)) or {}
    payload = response_payload if isinstance(response_payload, dict) else _json_load(_json_dumps(response_payload)) or {}

    for key in _REFERENCE_PRICE_KEYS:
        candidate = _safe_float(payload.get(key))
        if candidate and candidate > 0:
            return candidate

    for key in _REFERENCE_PRICE_KEYS:
        candidate = _safe_float(meta.get(key))
        if candidate and candidate > 0:
            return candidate
    return None


def _compute_slippage_bps(side: str, reference_price: Optional[float], fill_price: Optional[float]) -> Optional[float]:
    if not reference_price or reference_price <= 0 or not fill_price or fill_price <= 0:
        return None
    side_norm = str(side or "").strip().upper()
    if side_norm == "SELL":
        return ((reference_price - fill_price) / reference_price) * 10_000.0
    return ((fill_price - reference_price) / reference_price) * 10_000.0


def _compute_latency_ms(dispatch_at: Optional[str], broker_timestamp: Optional[str]) -> Optional[float]:
    start = _parse_iso_datetime(dispatch_at)
    end = _parse_iso_datetime(broker_timestamp)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds() * 1000.0, 0.0)


def _default_correlation_id(action_type: str, instrument: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{action_type}:{instrument}:{stamp}:{uuid.uuid4().hex[:8]}"


def _can_transition(from_status: OrderIntentStatus, to_status: OrderIntentStatus) -> bool:
    if to_status == from_status:
        return True
    return to_status in _TRANSITION_RULES.get(from_status, set())


def ensure_order_intent_schema(db_path: str = DB_PATH) -> None:
    """Create order intent envelope tables if they do not exist."""
    conn = get_conn(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS order_intents (
            intent_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            sleeve TEXT NOT NULL,
            account_type TEXT NOT NULL,
            broker_target TEXT NOT NULL,
            instrument TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            order_type TEXT NOT NULL,
            risk_tags TEXT,
            metadata TEXT,
            status TEXT NOT NULL,
            actor TEXT NOT NULL,
            latest_attempt INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_order_intents_created_at ON order_intents(created_at);
        CREATE INDEX IF NOT EXISTS idx_order_intents_updated_at ON order_intents(updated_at);
        CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents(status);
        CREATE INDEX IF NOT EXISTS idx_order_intents_corr ON order_intents(correlation_id);

        CREATE TABLE IF NOT EXISTS order_intent_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            actor TEXT NOT NULL,
            request_payload TEXT,
            response_payload TEXT,
            error_code TEXT,
            error_message TEXT,
            UNIQUE(intent_id, attempt)
        );

        CREATE INDEX IF NOT EXISTS idx_order_intent_attempts_intent_id ON order_intent_attempts(intent_id);
        CREATE INDEX IF NOT EXISTS idx_order_intent_attempts_status ON order_intent_attempts(status);

        CREATE TABLE IF NOT EXISTS order_intent_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_id TEXT NOT NULL,
            transition_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            attempt INTEGER,
            request_payload TEXT,
            response_payload TEXT,
            error_code TEXT,
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_order_intent_transitions_intent_id
            ON order_intent_transitions(intent_id);
        CREATE INDEX IF NOT EXISTS idx_order_intent_transitions_time
            ON order_intent_transitions(transition_at);

        CREATE TABLE IF NOT EXISTS order_execution_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            event_at TEXT NOT NULL,
            status TEXT NOT NULL,
            actor TEXT NOT NULL,
            broker_target TEXT NOT NULL,
            account_type TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            sleeve TEXT NOT NULL,
            instrument TEXT NOT NULL,
            side TEXT NOT NULL,
            qty_requested REAL NOT NULL,
            qty_filled REAL NOT NULL DEFAULT 0,
            reference_price REAL,
            fill_price REAL,
            slippage_bps REAL,
            dispatch_latency_ms REAL,
            notional_requested REAL,
            notional_filled REAL,
            error_code TEXT,
            error_message TEXT,
            metadata TEXT,
            UNIQUE(intent_id, attempt)
        );

        CREATE INDEX IF NOT EXISTS idx_oem_event_at ON order_execution_metrics(event_at);
        CREATE INDEX IF NOT EXISTS idx_oem_status ON order_execution_metrics(status);
        CREATE INDEX IF NOT EXISTS idx_oem_broker ON order_execution_metrics(broker_target);
        CREATE INDEX IF NOT EXISTS idx_oem_instrument ON order_execution_metrics(instrument);
        CREATE INDEX IF NOT EXISTS idx_oem_strategy ON order_execution_metrics(strategy_id);
        """
    )
    conn.commit()
    conn.close()


def create_order_intent_envelope(
    intent: OrderIntent,
    action_type: str,
    max_attempts: int = 1,
    request_payload: Any = None,
    actor: str = "system",
    intent_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    action_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """
    Create a new order intent + initial queued transition and linked order_action record.
    """
    ensure_order_intent_schema(db_path)
    actor_value = normalize_actor(actor)
    intent.validate()

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    created_at = _utc_now()
    intent_id_value = intent_id or uuid.uuid4().hex
    action_id_value = action_id or uuid.uuid4().hex
    correlation_id_value = correlation_id or _default_correlation_id(action_type, intent.instrument)
    request_json = _json_dumps(request_payload)

    # Keep compatibility with existing action state machine.
    create_order_action(
        action_id=action_id_value,
        correlation_id=correlation_id_value,
        action_type=action_type,
        ticker=intent.instrument,
        max_attempts=max_attempts,
        request_payload=request_json,
        db_path=db_path,
    )

    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO order_intents
           (intent_id, created_at, updated_at, correlation_id, action_id, strategy_id,
            strategy_version, sleeve, account_type, broker_target, instrument, side,
            qty, order_type, risk_tags, metadata, status, actor, latest_attempt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            intent_id_value,
            created_at,
            created_at,
            correlation_id_value,
            action_id_value,
            intent.strategy_id,
            intent.strategy_version,
            intent.sleeve,
            intent.account_type.value,
            intent.broker_target,
            intent.instrument,
            intent.side.value,
            intent.qty,
            intent.order_type.value,
            _json_dumps(intent.risk_tags),
            _json_dumps(intent.metadata),
            OrderIntentStatus.QUEUED.value,
            actor_value,
        ),
    )
    conn.execute(
        """INSERT INTO order_intent_attempts
           (intent_id, attempt, created_at, updated_at, status, actor, request_payload)
           VALUES (?, 0, ?, ?, ?, ?, ?)""",
        (
            intent_id_value,
            created_at,
            created_at,
            OrderIntentStatus.QUEUED.value,
            actor_value,
            request_json,
        ),
    )
    conn.execute(
        """INSERT INTO order_intent_transitions
           (intent_id, transition_at, actor, from_status, to_status, attempt, request_payload)
           VALUES (?, ?, ?, NULL, ?, 0, ?)""",
        (
            intent_id_value,
            created_at,
            actor_value,
            OrderIntentStatus.QUEUED.value,
            request_json,
        ),
    )
    conn.commit()
    conn.close()
    return get_order_intent(intent_id_value, db_path=db_path) or {}


def transition_order_intent(
    intent_id: str,
    status: str | OrderIntentStatus,
    attempt: Optional[int] = None,
    actor: str = "system",
    request_payload: Any = None,
    response_payload: Any = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    recoverable: Optional[bool] = None,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """
    Persist one lifecycle transition for both order intent envelope and order_actions.
    """
    ensure_order_intent_schema(db_path)
    actor_value = normalize_actor(actor)
    status_value = normalize_status(status)
    now = _utc_now()

    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM order_intents WHERE intent_id=?",
        (intent_id,),
    ).fetchone()
    if not row:
        conn.close()
        raise KeyError(f"order intent '{intent_id}' not found")

    current = dict(row)
    from_status = normalize_status(current["status"])
    if not _can_transition(from_status, status_value):
        conn.close()
        raise ValueError(f"Invalid transition {from_status.value} -> {status_value.value}")

    attempt_value = int(attempt) if attempt is not None else int(current.get("latest_attempt", 0) or 0)
    if attempt_value < 0:
        conn.close()
        raise ValueError("attempt must be >= 0")

    request_json = _json_dumps(request_payload)
    response_json = _json_dumps(response_payload)
    action_id = str(current.get("action_id", "") or "")

    # Keep existing order_actions state synchronized.
    update_order_action(
        action_id=action_id,
        status=status_value.value,
        attempt=attempt_value,
        recoverable=recoverable if recoverable is not None else (status_value == OrderIntentStatus.RETRYING),
        error_code=error_code,
        error_message=error_message,
        result_payload=response_json,
        db_path=db_path,
    )

    conn.execute(
        """INSERT INTO order_intent_attempts
           (intent_id, attempt, created_at, updated_at, status, actor, request_payload,
            response_payload, error_code, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(intent_id, attempt) DO UPDATE SET
               updated_at=excluded.updated_at,
               status=excluded.status,
               actor=excluded.actor,
               request_payload=COALESCE(excluded.request_payload, order_intent_attempts.request_payload),
               response_payload=COALESCE(excluded.response_payload, order_intent_attempts.response_payload),
               error_code=COALESCE(excluded.error_code, order_intent_attempts.error_code),
               error_message=COALESCE(excluded.error_message, order_intent_attempts.error_message)""",
        (
            intent_id,
            attempt_value,
            now,
            now,
            status_value.value,
            actor_value,
            request_json,
            response_json,
            error_code,
            error_message,
        ),
    )
    conn.execute(
        """INSERT INTO order_intent_transitions
           (intent_id, transition_at, actor, from_status, to_status, attempt,
            request_payload, response_payload, error_code, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            intent_id,
            now,
            actor_value,
            from_status.value,
            status_value.value,
            attempt_value,
            request_json,
            response_json,
            error_code,
            error_message,
        ),
    )
    conn.execute(
        """UPDATE order_intents
           SET updated_at=?,
               status=?,
               actor=?,
               latest_attempt=?
           WHERE intent_id=?""",
        (
            now,
            status_value.value,
            actor_value,
            max(int(current.get("latest_attempt", 0) or 0), attempt_value),
            intent_id,
        ),
    )
    conn.commit()
    conn.close()
    return get_order_intent(intent_id, db_path=db_path) or {}


def get_order_intent(intent_id: str, db_path: str = DB_PATH) -> Optional[dict[str, Any]]:
    """Get one order intent by ID."""
    ensure_order_intent_schema(db_path)
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM order_intents WHERE intent_id=?",
        (intent_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    payload = dict(row)
    payload["risk_tags"] = _json_load(payload.get("risk_tags"))
    payload["metadata"] = _json_load(payload.get("metadata"))
    return payload


def get_order_intents(
    limit: int = 100,
    status: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    """Get recent order intents, optionally filtered by status."""
    ensure_order_intent_schema(db_path)
    conn = get_conn(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM order_intents WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (normalize_status(status).value, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM order_intents ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    items = []
    for row in rows:
        payload = dict(row)
        payload["risk_tags"] = _json_load(payload.get("risk_tags"))
        payload["metadata"] = _json_load(payload.get("metadata"))
        items.append(payload)
    return items


def get_dispatchable_order_intents(
    limit: int = 100,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    """
    Get queued/retrying intents that still have attempt budget remaining.

    Returns oldest-first rows joined with order action retry metadata:
      - `action_id`
      - `max_attempts`
      - `action_attempt`
    """
    ensure_order_intent_schema(db_path)
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT oi.*,
                  oa.max_attempts AS max_attempts,
                  oa.attempt AS action_attempt
           FROM order_intents oi
           JOIN order_actions oa ON oi.action_id = oa.id
           WHERE oi.status IN (?, ?)
             AND oi.latest_attempt < oa.max_attempts
           ORDER BY oi.created_at ASC
           LIMIT ?""",
        (
            OrderIntentStatus.QUEUED.value,
            OrderIntentStatus.RETRYING.value,
            limit,
        ),
    ).fetchall()
    conn.close()

    items = []
    for row in rows:
        payload = dict(row)
        payload["risk_tags"] = _json_load(payload.get("risk_tags"))
        payload["metadata"] = _json_load(payload.get("metadata"))
        payload["max_attempts"] = int(payload.get("max_attempts", 1) or 1)
        payload["action_attempt"] = int(payload.get("action_attempt", 0) or 0)
        items.append(payload)
    return items


def claim_order_intent_for_dispatch(
    intent_id: str,
    attempt: int,
    actor: str = "system",
    request_payload: Any = None,
    db_path: str = DB_PATH,
) -> bool:
    """
    Atomically claim a queued/retrying intent and transition it to running.

    Returns True only when this caller wins the claim. Returns False if another
    dispatcher already claimed the intent or if the status is no longer dispatchable.
    """
    ensure_order_intent_schema(db_path)
    actor_value = normalize_actor(actor)
    attempt_value = int(attempt)
    if attempt_value < 1:
        raise ValueError("attempt must be >= 1")

    now = _utc_now()
    request_json = _json_dumps(request_payload)
    conn = get_conn(db_path)
    conn.execute("BEGIN IMMEDIATE")

    row = conn.execute(
        "SELECT * FROM order_intents WHERE intent_id=?",
        (intent_id,),
    ).fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return False

    current = dict(row)
    from_status = normalize_status(current["status"])
    if from_status not in {OrderIntentStatus.QUEUED, OrderIntentStatus.RETRYING}:
        conn.rollback()
        conn.close()
        return False

    # Guard against stale callers by requiring strictly increasing attempt.
    latest_attempt = int(current.get("latest_attempt", 0) or 0)
    if attempt_value <= latest_attempt:
        conn.rollback()
        conn.close()
        return False

    order_intent_update = conn.execute(
        """UPDATE order_intents
           SET updated_at=?, status=?, actor=?, latest_attempt=?
           WHERE intent_id=?
             AND status IN (?, ?)""",
        (
            now,
            OrderIntentStatus.RUNNING.value,
            actor_value,
            attempt_value,
            intent_id,
            OrderIntentStatus.QUEUED.value,
            OrderIntentStatus.RETRYING.value,
        ),
    )
    if order_intent_update.rowcount != 1:
        conn.rollback()
        conn.close()
        return False

    action_id = str(current.get("action_id", "") or "")
    action_update = conn.execute(
        """UPDATE order_actions
           SET updated_at=?, status=?, attempt=?, recoverable=0
           WHERE id=?
             AND status IN (?, ?)""",
        (
            now,
            OrderIntentStatus.RUNNING.value,
            attempt_value,
            action_id,
            OrderIntentStatus.QUEUED.value,
            OrderIntentStatus.RETRYING.value,
        ),
    )
    if action_update.rowcount != 1:
        conn.rollback()
        conn.close()
        return False

    conn.execute(
        """INSERT INTO order_intent_attempts
           (intent_id, attempt, created_at, updated_at, status, actor, request_payload)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(intent_id, attempt) DO UPDATE SET
               updated_at=excluded.updated_at,
               status=excluded.status,
               actor=excluded.actor,
               request_payload=COALESCE(excluded.request_payload, order_intent_attempts.request_payload)""",
        (
            intent_id,
            attempt_value,
            now,
            now,
            OrderIntentStatus.RUNNING.value,
            actor_value,
            request_json,
        ),
    )
    conn.execute(
        """INSERT INTO order_intent_transitions
           (intent_id, transition_at, actor, from_status, to_status, attempt, request_payload)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            intent_id,
            now,
            actor_value,
            from_status.value,
            OrderIntentStatus.RUNNING.value,
            attempt_value,
            request_json,
        ),
    )
    conn.commit()
    conn.close()
    return True


def get_order_intent_attempts(intent_id: str, db_path: str = DB_PATH) -> list[dict[str, Any]]:
    """Get all attempts for one order intent in ascending attempt order."""
    ensure_order_intent_schema(db_path)
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM order_intent_attempts WHERE intent_id=? ORDER BY attempt ASC",
        (intent_id,),
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        payload = dict(row)
        payload["request_payload"] = _json_load(payload.get("request_payload"))
        payload["response_payload"] = _json_load(payload.get("response_payload"))
        items.append(payload)
    return items


def get_order_intent_transitions(intent_id: str, db_path: str = DB_PATH) -> list[dict[str, Any]]:
    """Get ordered transition history for one order intent."""
    ensure_order_intent_schema(db_path)
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM order_intent_transitions WHERE intent_id=? ORDER BY id ASC",
        (intent_id,),
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        payload = dict(row)
        payload["request_payload"] = _json_load(payload.get("request_payload"))
        payload["response_payload"] = _json_load(payload.get("response_payload"))
        items.append(payload)
    return items


def record_execution_metric(
    intent_id: str,
    attempt: int,
    status: str | OrderIntentStatus,
    actor: str = "system",
    dispatch_at: Optional[str] = None,
    response_payload: Any = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path: str = DB_PATH,
) -> Optional[dict[str, Any]]:
    """
    Upsert one execution metric row for a dispatch attempt.

    This powers execution-quality reporting (fill rate, latency, slippage)
    using live dispatcher outcomes instead of backtest assumptions.
    """
    ensure_order_intent_schema(db_path)
    status_value = normalize_status(status).value
    actor_value = normalize_actor(actor)
    attempt_value = int(attempt)
    if attempt_value < 1:
        raise ValueError("attempt must be >= 1")

    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM order_intents WHERE intent_id=?",
        (intent_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None

    intent = dict(row)
    metadata = intent.get("metadata")
    if isinstance(metadata, str):
        metadata = _json_load(metadata)
    if not isinstance(metadata, dict):
        metadata = {}

    payload = response_payload or {}
    if not isinstance(payload, dict):
        payload = {"raw_response": payload}

    qty_requested = float(intent.get("qty", 0.0) or 0.0)
    qty_filled = _safe_float(payload.get("fill_qty")) or 0.0
    fill_price = _safe_float(payload.get("fill_price"))
    reference_price = _extract_reference_price(metadata, payload)
    slippage_bps = _compute_slippage_bps(str(intent.get("side", "")), reference_price, fill_price)

    broker_timestamp = payload.get("timestamp")
    event_at = (
        broker_timestamp
        if _parse_iso_datetime(broker_timestamp) is not None
        else _utc_now()
    )
    dispatch_latency_ms = _compute_latency_ms(dispatch_at, broker_timestamp)

    if reference_price and reference_price > 0:
        notional_requested = qty_requested * reference_price
    else:
        notional_requested = None
        logger.warning(
            "No valid reference_price for intent %s attempt %s; notional_requested set to None",
            intent_id, attempt,
        )
    notional_filled = (
        qty_filled * fill_price
        if fill_price is not None and fill_price > 0 and qty_filled > 0
        else 0.0
    )

    metric_metadata = {
        "order_id": payload.get("order_id"),
        "message": payload.get("message"),
        "response_timestamp": broker_timestamp,
    }

    conn.execute(
        """INSERT INTO order_execution_metrics
           (intent_id, action_id, correlation_id, attempt, event_at, status, actor,
            broker_target, account_type, strategy_id, sleeve, instrument, side,
            qty_requested, qty_filled, reference_price, fill_price, slippage_bps,
            dispatch_latency_ms, notional_requested, notional_filled,
            error_code, error_message, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(intent_id, attempt) DO UPDATE SET
               event_at=excluded.event_at,
               status=excluded.status,
               actor=excluded.actor,
               qty_filled=excluded.qty_filled,
               reference_price=COALESCE(excluded.reference_price, order_execution_metrics.reference_price),
               fill_price=COALESCE(excluded.fill_price, order_execution_metrics.fill_price),
               slippage_bps=COALESCE(excluded.slippage_bps, order_execution_metrics.slippage_bps),
               dispatch_latency_ms=COALESCE(excluded.dispatch_latency_ms, order_execution_metrics.dispatch_latency_ms),
               notional_requested=excluded.notional_requested,
               notional_filled=excluded.notional_filled,
               error_code=COALESCE(excluded.error_code, order_execution_metrics.error_code),
               error_message=COALESCE(excluded.error_message, order_execution_metrics.error_message),
               metadata=COALESCE(excluded.metadata, order_execution_metrics.metadata)""",
        (
            intent_id,
            str(intent.get("action_id", "")),
            str(intent.get("correlation_id", "")),
            attempt_value,
            event_at,
            status_value,
            actor_value,
            str(intent.get("broker_target", "")),
            str(intent.get("account_type", "")),
            str(intent.get("strategy_id", "")),
            str(intent.get("sleeve", "")),
            str(intent.get("instrument", "")),
            str(intent.get("side", "")),
            qty_requested,
            qty_filled,
            reference_price,
            fill_price,
            slippage_bps,
            dispatch_latency_ms,
            notional_requested,
            notional_filled,
            error_code,
            error_message,
            _json_dumps(metric_metadata),
        ),
    )
    conn.commit()

    metric_row = conn.execute(
        """SELECT * FROM order_execution_metrics
           WHERE intent_id=? AND attempt=?""",
        (intent_id, attempt_value),
    ).fetchone()
    conn.close()
    if not metric_row:
        return None
    item = dict(metric_row)
    item["metadata"] = _json_load(item.get("metadata"))
    return item


def get_execution_metrics(
    limit: int = 100,
    intent_id: Optional[str] = None,
    status: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict[str, Any]]:
    """Return recent execution metric rows for analytics/reporting."""
    ensure_order_intent_schema(db_path)
    conn = get_conn(db_path)
    clauses: list[str] = []
    params: list[Any] = []

    if intent_id:
        clauses.append("intent_id=?")
        params.append(intent_id)
    if status:
        clauses.append("status=?")
        params.append(normalize_status(status).value)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    rows = conn.execute(
        f"""SELECT * FROM order_execution_metrics
            {where_sql}
            ORDER BY event_at DESC, id DESC
            LIMIT ?""",
        (*params, int(limit)),
    ).fetchall()
    conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["metadata"] = _json_load(payload.get("metadata"))
        items.append(payload)
    return items
