"""Queued intent dispatcher.

Consumes queued/retrying order intents, submits to broker adapters, and
persists lifecycle transitions for completed/retrying/failed outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Callable, Optional

import config
from broker.base import BaseBroker, OrderResult
from broker.cityindex import CityIndexBroker
from broker.ibkr import IBKRBroker
from broker.ig import IGBroker
from broker.paper import PaperBroker
from data.order_intent_store import (
    get_dispatchable_order_intents,
    transition_order_intent,
)
from data.trade_db import DB_PATH
from execution.order_intent import OrderIntent, OrderSide

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

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "processed": self.processed,
            "completed": self.completed,
            "retried": self.retried,
            "failed": self.failed,
            "errors": self.errors,
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

        request_payload = {
            "dispatch_at": datetime.utcnow().isoformat(),
            "broker_target": row.get("broker_target"),
            "instrument": row.get("instrument"),
        }

        try:
            transition_order_intent(
                intent_id=intent_id,
                status="running",
                attempt=attempt,
                actor=self.actor,
                request_payload=request_payload,
                db_path=self.db_path,
            )
        except Exception as exc:
            summary.errors += 1
            logger.exception("Could not transition intent %s to running: %s", intent_id, exc)
            return

        try:
            intent = _row_to_order_intent(row)
            broker = self._resolve_connected_broker(intent.broker_target)
            result = self._submit_to_broker(broker, intent)
            payload = _order_result_payload(result)

            if result.success:
                transition_order_intent(
                    intent_id=intent_id,
                    status="completed",
                    attempt=attempt,
                    actor=self.actor,
                    response_payload=payload,
                    db_path=self.db_path,
                )
                summary.completed += 1
                return

            self._mark_failure(
                intent_id=intent_id,
                attempt=attempt,
                max_attempts=max_attempts,
                error_code="BROKER_REJECTED",
                error_message=result.message or "broker rejected order",
                response_payload=payload,
                summary=summary,
            )
        except Exception as exc:
            self._mark_failure(
                intent_id=intent_id,
                attempt=attempt,
                max_attempts=max_attempts,
                error_code="DISPATCH_ERROR",
                error_message=str(exc),
                response_payload={"exception": str(exc)},
                summary=summary,
            )

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
    ) -> None:
        status = "retrying" if attempt < max_attempts else "failed"
        recoverable = attempt < max_attempts

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

        if recoverable:
            summary.retried += 1
        else:
            summary.failed += 1


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
        is_demo = str(config.IG_ACC_TYPE or "DEMO").upper() != "LIVE"
        return IGBroker(is_demo=is_demo)
    if key == "ibkr":
        return IBKRBroker()
    if key in {"cityindex", "city_index"}:
        is_demo = str(config.BROKER_MODE or "paper").lower() != "live"
        return CityIndexBroker(is_demo=is_demo)

    raise ValueError(f"Unsupported broker target '{broker_name}'")
