"""Persistence helpers for unified order intents and audit envelopes."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Optional
import uuid

from data.trade_db import DB_PATH, create_order_action, get_conn, update_order_action
from execution.order_intent import OrderIntent, OrderIntentStatus, normalize_actor, normalize_status


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
