"""Queued intent dispatcher.

Consumes queued/retrying order intents, submits to broker adapters, and
persists lifecycle transitions for completed/retrying/failed outcomes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import config
from broker.base import BaseBroker, OrderResult
from broker.ibkr import IBKRBroker
from broker.ig import IGBroker
from broker.paper import PaperBroker
from data.order_intent_store import (
    claim_order_intent_for_dispatch,
    get_dispatchable_order_intents,
    get_dispatchable_order_intents_by_ids,
    record_execution_metric,
    transition_order_intent,
)
from data.trade_db import DB_PATH
from execution.order_intent import OrderIntent, OrderSide
from utils.datetime_utils import utc_now_naive_iso

logger = logging.getLogger(__name__)

BrokerResolver = Callable[[str], BaseBroker]


@dataclass
class DispatchRunSummary:
    """Result summary for one dispatcher cycle."""

    discovered: int = 0
    processed: int = 0
    completed: int = 0
    retried: int = 0
    failed: int = 0
    errors: int = 0
    claim_conflicts: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "processed": self.processed,
            "completed": self.completed,
            "retried": self.retried,
            "failed": self.failed,
            "errors": self.errors,
            "claim_conflicts": self.claim_conflicts,
        }


class IntentDispatcher:
    """Dispatches queued/retrying intents to broker adapters."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        broker_resolver: Optional[BrokerResolver] = None,
        actor: str = "system",
        disconnect_after_run: bool = True,
    ):
        self.db_path = db_path
        self.actor = actor
        self.disconnect_after_run = disconnect_after_run
        self._broker_resolver = broker_resolver or default_broker_resolver
        self._brokers: dict[str, BaseBroker] = {}

    def run_once(self, limit: int = 50) -> DispatchRunSummary:
        """Process at most ``limit`` dispatchable intents in created_at order."""
        queue = get_dispatchable_order_intents(limit=limit, db_path=self.db_path)
        return self._run_rows(queue)

    def run_intent_ids(self, intent_ids: list[str]) -> DispatchRunSummary:
        """Process a concrete set of dispatchable intent ids."""
        queue = get_dispatchable_order_intents_by_ids(intent_ids, db_path=self.db_path)
        return self._run_rows(queue)

    def _run_rows(self, queue: list[dict]) -> DispatchRunSummary:
        """Dispatch the provided queue rows in order."""
        summary = DispatchRunSummary(discovered=len(queue))

        try:
            for item in queue:
                summary.processed += 1
                self._dispatch_one(item, summary)
        finally:
            if self.disconnect_after_run:
                self.disconnect_all()

        return summary

    def disconnect_all(self) -> None:
        """Disconnect all cached brokers (best effort)."""
        for name, broker in list(self._brokers.items()):
            try:
                broker.disconnect()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                logger.warning("Broker disconnect failed (%s): %s", name, exc)
        self._brokers.clear()

    def _dispatch_one(self, row: dict, summary: DispatchRunSummary) -> None:
        intent_id = str(row["intent_id"])
        attempt = int(row.get("latest_attempt", 0) or 0) + 1
        max_attempts = int(row.get("max_attempts", 1) or 1)
        dispatch_at = utc_now_naive_iso()

        request_payload = {
            "dispatch_at": dispatch_at,
            "broker_target": row.get("broker_target"),
            "instrument": row.get("instrument"),
        }

        claimed = claim_order_intent_for_dispatch(
            intent_id=intent_id,
            attempt=attempt,
            actor=self.actor,
            request_payload=request_payload,
            db_path=self.db_path,
        )
        if not claimed:
            # Another dispatcher already claimed it, or status moved.
            summary.claim_conflicts += 1
            return

        try:
            intent = _row_to_order_intent(row)
            broker = self._resolve_connected_broker(intent.broker_target)
            result = self._submit_to_broker(broker, intent)
            payload = _order_result_payload(result)

            if result.success:
                persisted = self._persist_completed(
                    intent_id=intent_id,
                    attempt=attempt,
                    response_payload=payload,
                    summary=summary,
                )
                if persisted:
                    summary.completed += 1
                    self._record_metric(
                        intent_id=intent_id,
                        attempt=attempt,
                        status="completed",
                        dispatch_at=dispatch_at,
                        response_payload=payload,
                        summary=summary,
                    )
                else:
                    self._record_metric(
                        intent_id=intent_id,
                        attempt=attempt,
                        status="failed",
                        dispatch_at=dispatch_at,
                        response_payload=payload,
                        error_code="POST_SUBMIT_PERSIST_FAILED",
                        error_message="broker submitted but completion persistence failed",
                        summary=summary,
                    )
                return

            failure_status = self._mark_failure(
                intent_id=intent_id,
                attempt=attempt,
                max_attempts=max_attempts,
                error_code="BROKER_REJECTED",
                error_message=result.message or "broker rejected order",
                response_payload=payload,
                summary=summary,
            )
            if failure_status:
                self._record_metric(
                    intent_id=intent_id,
                    attempt=attempt,
                    status=failure_status,
                    dispatch_at=dispatch_at,
                    response_payload=payload,
                    error_code="BROKER_REJECTED",
                    error_message=result.message or "broker rejected order",
                    summary=summary,
                )
        except Exception as exc:
            failure_status = self._mark_failure(
                intent_id=intent_id,
                attempt=attempt,
                max_attempts=max_attempts,
                error_code="DISPATCH_ERROR",
                error_message=str(exc),
                response_payload={"exception": str(exc)},
                summary=summary,
            )
            if failure_status:
                self._record_metric(
                    intent_id=intent_id,
                    attempt=attempt,
                    status=failure_status,
                    dispatch_at=dispatch_at,
                    response_payload={"exception": str(exc)},
                    error_code="DISPATCH_ERROR",
                    error_message=str(exc),
                    summary=summary,
                )

    def _persist_completed(
        self,
        intent_id: str,
        attempt: int,
        response_payload: dict,
        summary: DispatchRunSummary,
    ) -> bool:
        """
        Persist successful submit as completed.

        If completion persistence fails after broker submit, best-effort move to
        terminal failed to prevent orphaned `running` intents being re-submitted.
        """
        try:
            transition_order_intent(
                intent_id=intent_id,
                status="completed",
                attempt=attempt,
                actor=self.actor,
                response_payload=response_payload,
                db_path=self.db_path,
            )
            return True
        except Exception as exc:
            summary.errors += 1
            logger.exception(
                "Could not persist completed for %s after broker submit: %s",
                intent_id, exc,
            )
            try:
                transition_order_intent(
                    intent_id=intent_id,
                    status="failed",
                    attempt=attempt,
                    actor=self.actor,
                    response_payload=response_payload,
                    error_code="POST_SUBMIT_PERSIST_FAILED",
                    error_message="broker submitted but completion persistence failed",
                    recoverable=False,
                    db_path=self.db_path,
                )
                summary.failed += 1
            except Exception as fallback_exc:  # pragma: no cover - defensive path
                summary.errors += 1
                logger.exception(
                    "Could not persist fallback failed state for %s: %s",
                    intent_id, fallback_exc,
                )
            return False

    def _resolve_connected_broker(self, broker_name: str) -> BaseBroker:
        key = str(broker_name or "").strip().lower()
        if not key:
            raise ValueError("broker_target is required")

        broker = self._brokers.get(key)
        if broker is None:
            broker = self._broker_resolver(key)
            self._brokers[key] = broker

        connected = broker.connect()
        if not connected:
            raise RuntimeError(f"Broker connect failed for '{key}'")
        return broker

    def _submit_to_broker(self, broker: BaseBroker, intent: OrderIntent) -> OrderResult:
        is_exit = bool((intent.metadata or {}).get("is_exit", False))

        if is_exit:
            return broker.close_position(intent.instrument, intent.strategy_id)
        if intent.side == OrderSide.BUY:
            return broker.place_long(intent.instrument, intent.qty, intent.strategy_id)
        return broker.place_short(intent.instrument, intent.qty, intent.strategy_id)

    def _mark_failure(
        self,
        intent_id: str,
        attempt: int,
        max_attempts: int,
        error_code: str,
        error_message: str,
        response_payload: dict,
        summary: DispatchRunSummary,
    ) -> Optional[str]:
        status = "retrying" if attempt < max_attempts else "failed"
        recoverable = attempt < max_attempts

        try:
            transition_order_intent(
                intent_id=intent_id,
                status=status,
                attempt=attempt,
                actor=self.actor,
                response_payload=response_payload,
                error_code=error_code,
                error_message=error_message,
                recoverable=recoverable,
                db_path=self.db_path,
            )
        except Exception as exc:
            summary.errors += 1
            logger.exception(
                "Could not persist failure transition for %s: %s",
                intent_id, exc,
            )
            return None

        if recoverable:
            summary.retried += 1
        else:
            summary.failed += 1
        return status

    def _record_metric(
        self,
        intent_id: str,
        attempt: int,
        status: str,
        dispatch_at: str,
        response_payload: dict,
        summary: DispatchRunSummary,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        try:
            record_execution_metric(
                intent_id=intent_id,
                attempt=attempt,
                status=status,
                actor=self.actor,
                dispatch_at=dispatch_at,
                response_payload=response_payload,
                error_code=error_code,
                error_message=error_message,
                db_path=self.db_path,
            )
        except Exception as exc:
            summary.errors += 1
            logger.exception(
                "Could not persist execution metric for %s/%s: %s",
                intent_id,
                attempt,
                exc,
            )


def _row_to_order_intent(row: dict) -> OrderIntent:
    return OrderIntent(
        strategy_id=row["strategy_id"],
        strategy_version=row["strategy_version"],
        sleeve=row["sleeve"],
        account_type=row["account_type"],
        broker_target=row["broker_target"],
        instrument=row["instrument"],
        side=row["side"],
        qty=row["qty"],
        order_type=row["order_type"],
        risk_tags=row.get("risk_tags") or [],
        metadata=row.get("metadata") or {},
    )


def _order_result_payload(result: OrderResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "success": bool(result.success),
        "order_id": result.order_id,
        "fill_price": float(result.fill_price or 0.0),
        "fill_qty": float(result.fill_qty or 0.0),
        "message": result.message,
    }
    if result.timestamp is not None:
        payload["timestamp"] = result.timestamp.isoformat()
    return payload


def default_broker_resolver(broker_name: str) -> BaseBroker:
    """Construct a broker adapter for a broker target name."""
    key = str(broker_name or "").strip().lower()

    if key == "paper":
        return PaperBroker()
    if key == "ig":
        return IGBroker(is_demo=config.ig_broker_is_demo())
    if key == "ibkr":
        return IBKRBroker()
    raise ValueError(f"Unsupported broker target '{broker_name}'")
