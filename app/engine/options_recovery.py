"""Startup recovery and state persistence for OptionsBot."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import config
from data.trade_db import (
    log_event, log_control_action,
    get_open_option_positions, get_order_actions_by_statuses,
    update_order_action, load_strategy_state, save_strategy_state,
    get_active_strategy_parameter_set,
)

logger = logging.getLogger(__name__)
_RECOVERABLE_OPTIONS_ACTION_TYPES = {"open_spread", "close_spread"}


class OptionsRecoveryMixin:

    def _startup_recover(self):
        from app.engine.options_bot import UK
        pending = get_order_actions_by_statuses(["queued", "running", "retrying"], limit=500)
        if not pending:
            return
        pending = [
            action for action in pending
            if str(action.get("action_type", "") or "") in _RECOVERABLE_OPTIONS_ACTION_TYPES
        ]
        if not pending:
            return

        db_positions = {p["spread_id"]: p for p in get_open_option_positions()}
        broker_deal_ids = set()
        try:
            for broker_pos in self.broker.get_positions():
                deal_id = str(getattr(broker_pos, "deal_id", "") or "").strip()
                if deal_id:
                    broker_deal_ids.add(deal_id)
        except Exception as exc:
            logger.error(f"Startup recovery: broker positions fetch failed: {exc}")
            log_event("ERROR", "Startup recovery failed to fetch broker positions", str(exc),
                      strategy="IBS Credit Spreads")

        completed = 0
        failed = 0

        for action in pending:
            action_id = str(action.get("id"))
            action_type = str(action.get("action_type", ""))
            spread_id = str(action.get("spread_id", "") or "")
            attempt = int(action.get("attempt", 0) or 0)
            max_attempts = int(action.get("max_attempts", 1) or 1)
            pos = db_positions.get(spread_id)

            if action_type == "open_spread":
                if pos:
                    short_id = str(pos.get("short_deal_id", "") or "")
                    long_id = str(pos.get("long_deal_id", "") or "")
                    short_ok = (not short_id) or (short_id in broker_deal_ids)
                    long_ok = (not long_id) or (long_id in broker_deal_ids)
                    if short_ok and long_ok:
                        update_order_action(
                            action_id=action_id,
                            status="completed",
                            attempt=max(attempt, 1),
                            recoverable=False,
                            error_code="",
                            error_message="",
                            result_payload=json.dumps({
                                "startup_recovered": True,
                                "reason": "Open spread exists in DB and broker position set is consistent",
                            }),
                        )
                        completed += 1
                        continue
                update_order_action(
                    action_id=action_id,
                    status="failed",
                    attempt=max(attempt, max_attempts),
                    recoverable=True,
                    error_code="STARTUP_INCOMPLETE_OPEN",
                    error_message="Pending open_spread not confirmed during startup recovery",
                )
                failed += 1
                continue

            if action_type == "close_spread":
                if not pos:
                    update_order_action(
                        action_id=action_id,
                        status="completed",
                        attempt=max(attempt, 1),
                        recoverable=False,
                        error_code="",
                        error_message="",
                        result_payload=json.dumps({
                            "startup_recovered": True,
                            "reason": "Spread already absent from DB after close action",
                        }),
                    )
                    completed += 1
                    continue
                update_order_action(
                    action_id=action_id,
                    status="failed",
                    attempt=max(attempt, max_attempts),
                    recoverable=True,
                    error_code="STARTUP_INCOMPLETE_CLOSE",
                    error_message="Pending close_spread still present in DB during startup recovery",
                )
                failed += 1
                continue

        summary = f"pending={len(pending)} recovered={completed} unresolved={failed}"
        logger.info(f"Startup recovery complete ({summary})")
        log_control_action(
            action="startup_recovery",
            value=summary,
            reason="Recovered pending order action states after startup",
            actor="system",
        )
        if failed:
            log_event("ERROR", "Startup recovery left unresolved actions", summary, strategy="IBS Credit Spreads")
        else:
            log_event("POSITION", "Startup recovery complete", summary, strategy="IBS Credit Spreads")

    def _persist_control_state(self):
        payload = {
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "risk_throttle_pct": self.risk_throttle_pct,
            "market_cooldowns": {
                ticker: expires.isoformat()
                for ticker, expires in self.market_cooldowns.items()
            },
        }
        save_strategy_state("risk_overrides", json.dumps(payload))

    def _load_control_state(self):
        from app.engine.options_bot import UK
        raw = load_strategy_state("risk_overrides")
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid risk_overrides state payload; ignoring")
            return

        self.kill_switch_active = bool(payload.get("kill_switch_active", False))
        self.kill_switch_reason = str(payload.get("kill_switch_reason", "") or "")
        throttle = float(payload.get("risk_throttle_pct", 1.0) or 1.0)
        self.risk_throttle_pct = min(1.0, max(0.1, throttle))
        self.market_cooldowns = {}
        now = datetime.now(UK)
        for ticker, iso_value in (payload.get("market_cooldowns") or {}).items():
            try:
                expires = datetime.fromisoformat(str(iso_value))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UK)
                if expires > now:
                    self.market_cooldowns[str(ticker).upper()] = expires
            except Exception:
                continue

    def _load_open_positions(self):
        db_positions = get_open_option_positions()
        for pos in db_positions:
            self.open_spreads[pos["spread_id"]] = pos
        if db_positions:
            logger.info(f"Loaded {len(db_positions)} open option positions from DB")
            self.safety.update_state(self.safety.equity, list(self.open_spreads.values()))

    def _load_strategy_parameters(self):
        self.strategy_params = dict(config.IBS_CREDIT_SPREAD_PARAMS)
        self.active_param_set_id = None
        self.active_param_set_status = "default"

        preferred_status = "live" if self.mode == "live" else "shadow"
        row = get_active_strategy_parameter_set(config.DEFAULT_STRATEGY_KEY, status=preferred_status)
        if not row and self.mode != "live":
            row = get_active_strategy_parameter_set(config.DEFAULT_STRATEGY_KEY, status="staged_live")
        if not row:
            return

        try:
            payload = json.loads(row.get("parameters_payload") or "{}")
        except json.JSONDecodeError:
            logger.warning("Invalid strategy parameter set payload for %s; using defaults", row.get("id"))
            return

        if not isinstance(payload, dict):
            logger.warning("Parameter set payload is not an object for %s; using defaults", row.get("id"))
            return

        self.strategy_params.update(payload)
        self.active_param_set_id = str(row.get("id") or "")
        self.active_param_set_status = str(row.get("status") or "unknown")
