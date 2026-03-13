"""Spread entry/exit/monitoring for OptionsBot."""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import config
from strategies.ibs_credit_spreads import OptionPosition
from portfolio.risk import calc_option_spread_size
from notifications import notifier
from data.trade_db import (
    log_event, log_trade, log_shadow_trade,
    upsert_option_position, close_option_position,
    create_order_action, update_order_action,
)

if TYPE_CHECKING:
    from strategies.ibs_credit_spreads import CreditSpreadSignal

logger = logging.getLogger(__name__)


class OptionsSpreadsMixin:

    def _enter_spread(self, ticker: str, sig: CreditSpreadSignal,
                      current_price: float):
        from app.engine.options_bot import UK, ORDER_ACTION_MAX_ATTEMPTS

        if self.kill_switch_active:
            detail = self.kill_switch_reason or "Kill switch active"
            logger.warning(f"  {ticker}: blocked by kill switch — {detail}")
            log_event(
                "REJECTION",
                f"{ticker} — Entry blocked by kill switch",
                detail,
                ticker=ticker,
                strategy="IBS Credit Spreads",
            )
            notifier.trade_rejected(ticker, f"Kill switch active: {detail}")
            return

        remaining = self._cooldown_remaining(ticker)
        if remaining:
            mins_left = max(1, int(remaining.total_seconds() // 60))
            reason = f"Market cooldown active ({mins_left}m remaining)"
            logger.info(f"  {ticker}: {reason}")
            log_event("REJECTION", f"{ticker} — Entry blocked by cooldown", reason,
                      ticker=ticker, strategy="IBS Credit Spreads")
            notifier.trade_rejected(ticker, reason)
            return

        # --- Drawdown breaker: auto-halt on excessive daily/weekly losses ---
        dd_decision = self._check_drawdown_gate()
        if dd_decision and dd_decision.action.value == "halt":
            reason = f"Drawdown breaker HALT: {dd_decision.reason}"
            logger.warning(f"  {ticker}: {reason}")
            log_event("REJECTION", f"{ticker} — Drawdown breaker halted entry",
                      reason, ticker=ticker, strategy="IBS Credit Spreads")
            notifier.trade_rejected(ticker, reason)
            # Auto-engage kill switch so the halt persists until manual reset
            if not self.kill_switch_active:
                self.set_kill_switch(True, reason=reason, actor="drawdown_breaker")
            return

        spread_width = sig.short_strike - sig.long_strike
        if spread_width <= 0:
            logger.warning(f"  {ticker}: invalid spread width {spread_width}")
            return

        # --- Spread width sanity check: reject absurdly wide spreads ---
        max_spread_width_pct = config.OPTIONS_SAFETY.get("max_spread_width_pct", 10.0)
        if current_price > 0:
            width_pct = (spread_width / current_price) * 100
            if width_pct > max_spread_width_pct:
                reason = (
                    f"Spread width {spread_width:.0f} is {width_pct:.1f}% of price "
                    f"{current_price:.0f} (max {max_spread_width_pct}%)"
                )
                logger.warning(f"  {ticker}: {reason}")
                log_event("REJECTION", f"{ticker} — Spread too wide", reason,
                          ticker=ticker, strategy="IBS Credit Spreads")
                notifier.trade_rejected(ticker, reason)
                return

        estimated_premium = spread_width * 0.30

        max_loss_per_contract = spread_width - estimated_premium

        size_result = calc_option_spread_size(
            equity=self.safety.equity,
            spread_width=spread_width,
            premium=estimated_premium,
            max_risk_pct=config.OPTIONS_SAFETY["max_risk_per_trade_pct"],
            kelly_fraction=self.strategy_params.get("kelly_fraction", 0.25),
            max_size=float(config.OPTIONS_SAFETY.get("max_contracts_per_trade", 10)),
        )

        if size_result.stake_per_point <= 0:
            logger.info(f"  {ticker}: sizing returned 0 — {size_result.notes}")
            return

        num_contracts = int(size_result.stake_per_point)
        if self.risk_throttle_pct < 1.0:
            throttled = max(1, int(num_contracts * self.risk_throttle_pct))
            if throttled < num_contracts:
                logger.info(
                    f"  {ticker}: risk throttle {self.risk_throttle_pct:.2f} applied "
                    f"({num_contracts} -> {throttled} contracts)"
                )
                num_contracts = throttled
        total_risk = num_contracts * max_loss_per_contract

        viable, reason = self.safety.check_order_viability(
            proposed_risk=total_risk,
            proposed_size=num_contracts,
            premium_pct=(estimated_premium / spread_width * 100) if spread_width > 0 else 0,
        )

        if not viable:
            logger.info(f"  {ticker}: BLOCKED by safety — {reason}")
            log_event("REJECTION", f"{ticker} — Safety blocked: {reason}",
                      ticker=ticker, strategy="IBS Credit Spreads")
            notifier.trade_rejected(ticker, reason)
            return

        if self.is_shadow:
            logger.info(
                f"  [SHADOW] {ticker}: Would place {sig.action} — "
                f"{num_contracts} contracts, risk=£{total_risk:.0f}"
            )
            log_shadow_trade(
                ticker=ticker,
                strategy="IBS Credit Spreads",
                action=sig.action,
                short_strike=sig.short_strike,
                long_strike=sig.long_strike,
                spread_width=spread_width,
                estimated_premium=estimated_premium,
                max_loss=max_loss_per_contract,
                size=num_contracts,
                reason=sig.reason,
            )
            notifier.shadow_trade(
                ticker, sig.action, sig.short_strike, sig.long_strike, sig.reason,
            )
            log_event("SIGNAL",
                      f"[SHADOW] {ticker} — {sig.action}",
                      f"Short={sig.short_strike}, Long={sig.long_strike}, "
                      f"{num_contracts} contracts, risk=£{total_risk:.0f}. "
                      f"Reason: {sig.reason}",
                      ticker=ticker, strategy="IBS Credit Spreads")
            return

        logger.info(f"  {ticker}: PLACING LIVE ORDER — {num_contracts} contracts")
        spread_id = f"{ticker}-{uuid.uuid4().hex[:8]}"
        action_id = uuid.uuid4().hex
        correlation_id = self._new_correlation_id("open_spread", ticker)
        max_attempts = max(1, int(config.OPTIONS_SAFETY.get("order_max_attempts", ORDER_ACTION_MAX_ATTEMPTS)))
        option_type = "PUT" if sig.action == "open_put_spread" else "CALL"
        option_side_text = "put" if option_type == "PUT" else "call"

        create_order_action(
            action_id=action_id,
            correlation_id=correlation_id,
            action_type="open_spread",
            ticker=ticker,
            spread_id=spread_id,
            max_attempts=max_attempts,
            request_payload=json.dumps({
                "ticker": ticker,
                "signal_action": sig.action,
                "short_target": sig.short_strike,
                "long_target": sig.long_strike,
                "size": num_contracts,
                "reason": sig.reason,
            }),
        )

        short_option = None
        long_option = None
        result = None
        last_error = "Unknown order failure"
        last_code = "UNKNOWN_EXECUTION_ERROR"

        for attempt in range(1, max_attempts + 1):
            update_order_action(
                action_id=action_id,
                status="running",
                attempt=attempt,
                recoverable=False,
                error_code="",
                error_message="",
            )
            attempt_correlation = f"{correlation_id}-a{attempt}"
            try:
                option_cfg = config.OPTION_EPIC_PATTERNS.get(ticker)
                if not option_cfg:
                    last_error = "No option EPIC pattern configured"
                    last_code, recoverable = self._classify_order_error(last_error, "NO_OPTION_CONFIG")
                else:
                    search_term = f"{option_cfg['search']} {option_side_text}"
                    options = self.broker.search_option_markets(search_term)
                    if not options:
                        last_error = f"No options found on IG for '{search_term}'"
                        last_code, recoverable = self._classify_order_error(last_error, "OPTIONS_NOT_FOUND")
                    else:
                        # Scale ETF-based strikes to IG index/commodity scale
                        strike_scale = option_cfg.get("strike_scale", 1.0)
                        scaled_short = round(sig.short_strike * strike_scale)
                        scaled_long = round(sig.long_strike * strike_scale)
                        logger.info(
                            f"    {ticker}: IG returned {len(options)} options for '{search_term}', "
                            f"looking for {option_type} near short={scaled_short} long={scaled_long} "
                            f"(raw={sig.short_strike}/{sig.long_strike}, scale={strike_scale}x)"
                        )
                        short_option = self._find_closest_option(options, scaled_short, option_type)
                        long_option = self._find_closest_option(options, scaled_long, option_type)
                        if not short_option or not long_option:
                            available = sorted(set(
                                o.strike for o in options
                                if o.option_type == option_type and o.strike > 0
                            ))
                            last_error = (
                                f"Couldn't find matching {option_type} options "
                                f"(scaled_short={scaled_short}, scaled_long={scaled_long}, "
                                f"raw={sig.short_strike}/{sig.long_strike}, scale={strike_scale}x, "
                                f"available_strikes={available[:10]})"
                            )
                            last_code, recoverable = self._classify_order_error(last_error, "OPTION_MATCH_FAILED")
                        elif short_option.epic == long_option.epic:
                            last_error = (
                                f"Both legs matched same epic {short_option.epic} "
                                f"(short_target={sig.short_strike}, long_target={sig.long_strike}, "
                                f"matched_strike={short_option.strike}) — IG may lack sufficient strikes"
                            )
                            last_code, recoverable = self._classify_order_error(last_error, "SAME_EPIC_BOTH_LEGS")
                        else:
                            logger.info(f"    Short leg: {short_option.epic} (strike={short_option.strike})")
                            logger.info(f"    Long leg: {long_option.epic} (strike={long_option.strike})")

                            short_check = self.broker.validate_option_leg(short_option.epic, float(num_contracts))
                            if not short_check.get("ok"):
                                hint = str(short_check.get("code", "LEG_VALIDATION_FAILED"))
                                last_error = str(short_check.get("message", "Short leg failed validation"))
                                last_code, recoverable = self._classify_order_error(last_error, hint)
                            else:
                                long_check = self.broker.validate_option_leg(long_option.epic, float(num_contracts))
                                if not long_check.get("ok"):
                                    hint = str(long_check.get("code", "LEG_VALIDATION_FAILED"))
                                    last_error = str(long_check.get("message", "Long leg failed validation"))
                                    last_code, recoverable = self._classify_order_error(last_error, hint)
                                else:
                                    result = self.broker.place_option_spread(
                                        short_epic=short_option.epic,
                                        long_epic=long_option.epic,
                                        size=float(num_contracts),
                                        ticker=ticker,
                                        strategy="IBS Credit Spreads",
                                        correlation_id=attempt_correlation,
                                    )
                                    if result.success:
                                        update_order_action(
                                            action_id=action_id,
                                            status="completed",
                                            attempt=attempt,
                                            recoverable=False,
                                            error_code="",
                                            error_message="",
                                            result_payload=json.dumps({
                                                "short_deal_id": result.short_deal_id,
                                                "long_deal_id": result.long_deal_id,
                                                "short_fill_price": result.short_fill_price,
                                                "long_fill_price": result.long_fill_price,
                                                "net_premium": result.net_premium,
                                            }),
                                        )
                                        break
                                    last_error = result.message or "Spread order failed"
                                    last_code, recoverable = self._classify_order_error(last_error)
            except Exception as exc:
                last_error = str(exc)
                last_code, recoverable = self._classify_order_error(last_error)

            if result and result.success:
                break

            if recoverable and attempt < max_attempts:
                update_order_action(
                    action_id=action_id,
                    status="retrying",
                    attempt=attempt,
                    recoverable=True,
                    error_code=last_code,
                    error_message=last_error,
                )
                wait_s = self._retry_backoff_seconds(attempt)
                logger.warning(
                    f"  {ticker}: open attempt {attempt}/{max_attempts} failed "
                    f"({last_code}) — retrying in {wait_s:.0f}s: {last_error}"
                )
                time.sleep(wait_s)
                continue

            update_order_action(
                action_id=action_id,
                status="failed",
                attempt=attempt,
                recoverable=recoverable,
                error_code=last_code,
                error_message=last_error,
            )
            result = None
            break

        if not result or not result.success:
            logger.error(f"  {ticker}: spread order failed — {last_code}: {last_error}")
            log_event(
                "REJECTION",
                f"{ticker} — Order failed ({last_code})",
                last_error,
                ticker=ticker,
                strategy="IBS Credit Spreads",
            )
            notifier.error(f"{ticker}: order failed ({last_code}) — {last_error}")
            return

        actual_premium = result.net_premium
        actual_max_loss = spread_width - actual_premium if actual_premium > 0 else max_loss_per_contract

        # --- Fill-price anomaly check ---
        if actual_premium <= 0:
            logger.error(
                f"  {ticker}: NEGATIVE/ZERO premium ({actual_premium:.2f}) on fill — "
                f"spread may be inverted. Logging but NOT blocking (already filled)."
            )
            log_event("WARNING", f"{ticker} — Zero/negative premium on fill",
                      f"premium={actual_premium:.2f}, spread_width={spread_width:.1f}",
                      ticker=ticker, strategy="IBS Credit Spreads")
            notifier.error(
                f"{ticker}: FILL ANOMALY — premium={actual_premium:.2f} "
                f"(expected ~{estimated_premium:.1f}). Check positions!"
            )
        elif estimated_premium > 0:
            fill_deviation = abs(actual_premium - estimated_premium) / estimated_premium * 100
            if fill_deviation > 50:
                logger.warning(
                    f"  {ticker}: fill premium {actual_premium:.2f} deviates "
                    f"{fill_deviation:.0f}% from estimate {estimated_premium:.1f}"
                )
                log_event("WARNING", f"{ticker} — Fill premium deviation {fill_deviation:.0f}%",
                          f"actual={actual_premium:.2f}, expected={estimated_premium:.1f}",
                          ticker=ticker, strategy="IBS Credit Spreads")

        self.open_spreads[spread_id] = {
            "spread_id": spread_id,
            "ticker": ticker,
            "trade_type": sig.action.replace("open_", ""),
            "short_deal_id": result.short_deal_id,
            "long_deal_id": result.long_deal_id,
            "short_strike": short_option.strike,
            "long_strike": long_option.strike,
            "short_epic": short_option.epic,
            "long_epic": long_option.epic,
            "spread_width": spread_width,
            "premium_collected": actual_premium,
            "max_loss": actual_max_loss,
            "size": num_contracts,
            "entry_date": datetime.now().isoformat(),
            "bars_held": 0,
        }

        upsert_option_position(
            spread_id=spread_id, ticker=ticker, strategy="IBS Credit Spreads",
            trade_type=sig.action.replace("open_", ""),
            short_deal_id=result.short_deal_id, long_deal_id=result.long_deal_id,
            short_strike=short_option.strike, long_strike=long_option.strike,
            short_epic=short_option.epic, long_epic=long_option.epic,
            spread_width=spread_width, premium_collected=actual_premium,
            max_loss=actual_max_loss, size=num_contracts,
        )

        log_trade(
            ticker=ticker, strategy="IBS Credit Spreads",
            direction="SELL", action="OPEN", size=num_contracts,
            price=current_price,
            deal_id=result.short_deal_id,
            notes=(
                f"Credit spread: short={short_option.strike}, long={long_option.strike}, "
                f"premium={actual_premium:.1f}, max_loss={actual_max_loss:.1f}, "
                f"contracts={num_contracts}"
            ),
        )
        log_event("ORDER",
                  f"{ticker} — Credit spread opened",
                  f"Short={short_option.strike}, Long={long_option.strike}, "
                  f"{num_contracts} contracts, premium={actual_premium:.1f}pts",
                  ticker=ticker, strategy="IBS Credit Spreads")

        notifier.trade_entered(
            ticker, short_option.strike, long_option.strike,
            num_contracts, actual_premium, actual_max_loss * num_contracts,
        )

        logger.info(f"  {ticker}: SPREAD OPENED — {spread_id}")

    def _close_spread(self, spread: dict, reason: str) -> bool:
        from app.engine.options_bot import UK, ORDER_ACTION_MAX_ATTEMPTS

        ticker = spread["ticker"]
        spread_id = spread["spread_id"]

        if self.is_shadow:
            logger.info(f"  [SHADOW] {ticker}: Would close spread — {reason}")
            log_shadow_trade(
                ticker=ticker, strategy="IBS Credit Spreads",
                action="close", reason=reason,
                short_strike=spread.get("short_strike", 0),
                long_strike=spread.get("long_strike", 0),
            )
            self.open_spreads.pop(spread_id, None)
            return True

        action_id = uuid.uuid4().hex
        correlation_id = self._new_correlation_id("close_spread", ticker)
        max_attempts = max(1, int(config.OPTIONS_SAFETY.get("order_max_attempts", ORDER_ACTION_MAX_ATTEMPTS)))
        create_order_action(
            action_id=action_id,
            correlation_id=correlation_id,
            action_type="close_spread",
            ticker=ticker,
            spread_id=spread_id,
            max_attempts=max_attempts,
            request_payload=json.dumps({
                "spread_id": spread_id,
                "ticker": ticker,
                "short_deal_id": spread.get("short_deal_id"),
                "long_deal_id": spread.get("long_deal_id"),
                "size": spread.get("size"),
                "reason": reason,
            }),
        )

        result = None
        last_error = "Unknown close failure"
        last_code = "UNKNOWN_EXECUTION_ERROR"

        for attempt in range(1, max_attempts + 1):
            update_order_action(
                action_id=action_id,
                status="running",
                attempt=attempt,
                recoverable=False,
                error_code="",
                error_message="",
            )
            attempt_correlation = f"{correlation_id}-a{attempt}"

            try:
                result = self.broker.close_option_spread(
                    short_deal_id=spread["short_deal_id"],
                    long_deal_id=spread["long_deal_id"],
                    size=float(spread["size"]),
                    correlation_id=attempt_correlation,
                )
                if result.success:
                    update_order_action(
                        action_id=action_id,
                        status="completed",
                        attempt=attempt,
                        recoverable=False,
                        error_code="",
                        error_message="",
                        result_payload=json.dumps({
                            "short_deal_id": spread["short_deal_id"],
                            "long_deal_id": spread["long_deal_id"],
                            "short_fill_price": result.short_fill_price,
                            "long_fill_price": result.long_fill_price,
                            "net_close_cost": result.net_premium,
                        }),
                    )
                    break
                last_error = result.message or "Spread close failed"
                last_code, recoverable = self._classify_order_error(last_error)
            except Exception as exc:
                last_error = str(exc)
                last_code, recoverable = self._classify_order_error(last_error)

            if recoverable and attempt < max_attempts:
                update_order_action(
                    action_id=action_id,
                    status="retrying",
                    attempt=attempt,
                    recoverable=True,
                    error_code=last_code,
                    error_message=last_error,
                )
                wait_s = self._retry_backoff_seconds(attempt)
                logger.warning(
                    f"  {ticker}: close attempt {attempt}/{max_attempts} failed "
                    f"({last_code}) — retrying in {wait_s:.0f}s: {last_error}"
                )
                time.sleep(wait_s)
                continue

            update_order_action(
                action_id=action_id,
                status="failed",
                attempt=attempt,
                recoverable=recoverable,
                error_code=last_code,
                error_message=last_error,
            )
            result = None
            break

        if not result or not result.success:
            logger.error(f"  {ticker}: close failed — {last_code}: {last_error}")
            log_event("ERROR", f"{ticker} — Spread close failed ({last_code})",
                      last_error, ticker=ticker, strategy="IBS Credit Spreads")
            notifier.error(f"{ticker}: close failed ({last_code}) — {last_error}")
            return False

        entry_premium = spread.get("premium_collected", 0)
        exit_cost = abs(result.net_premium) if result.net_premium else 0
        pnl = (entry_premium - exit_cost) * spread["size"]

        close_option_position(spread_id, exit_pnl=pnl, exit_reason=reason)
        self.safety.record_closed_trade(pnl)
        self.open_spreads.pop(spread_id, None)

        log_trade(
            ticker=ticker, strategy="IBS Credit Spreads",
            direction="BUY", action="CLOSE", size=spread["size"],
            deal_id=spread["short_deal_id"], pnl=pnl,
            notes=f"Closed: {reason}, P&L=£{pnl:.2f}",
        )
        log_event("ORDER", f"{ticker} — Spread closed (£{pnl:+.2f})",
                  f"Reason: {reason}", ticker=ticker, strategy="IBS Credit Spreads")

        notifier.trade_closed(ticker, pnl, reason)
        logger.info(f"  {ticker}: SPREAD CLOSED — P&L=£{pnl:+.2f}")
        return True

    def _monitor_positions(self):
        if not self.open_spreads:
            return

        for spread_id, spread in list(self.open_spreads.items()):
            spread["bars_held"] = spread.get("bars_held", 0) + 1
            max_hold = self.strategy_params.get("max_hold_bars", 10)

            if spread["bars_held"] >= max_hold:
                logger.info(f"  {spread['ticker']}: max hold reached ({max_hold} bars)")
                self._close_spread(spread, f"Max hold {max_hold} bars (expiry)")

    def _get_open_spread(self, ticker: str) -> Optional[OptionPosition]:
        for s in self.open_spreads.values():
            if s["ticker"] == ticker:
                return OptionPosition(
                    trade_type=s.get("trade_type", "put_spread"),
                    entry_date=s.get("entry_date", ""),
                    entry_price=0,
                    short_strike=s.get("short_strike", 0),
                    long_strike=s.get("long_strike", 0),
                    premium_collected=s.get("premium_collected", 0),
                    spread_width=s.get("spread_width", 0),
                    max_loss=s.get("max_loss", 0),
                    contracts=int(s.get("size", 1)),
                    bars_held=s.get("bars_held", 0),
                    days_to_expiry=self.strategy_params.get("expiry_days", 10),
                )
        return None

    def _get_open_spread_dict(self, ticker: str) -> Optional[dict]:
        for s in self.open_spreads.values():
            if s["ticker"] == ticker:
                return s
        return None

    # Maximum allowed deviation between target strike and matched strike (5%)
    MAX_STRIKE_DEVIATION_PCT = 5.0

    def _find_closest_option(self, options: list, target_strike: float,
                             option_type: str):
        from broker.base import OptionMarket
        matching = [o for o in options if o.option_type == option_type and o.strike > 0]
        if not matching:
            # Diagnostic: log what we got vs what we need
            type_counts = {}
            zero_strike = 0
            for o in options:
                type_counts[o.option_type] = type_counts.get(o.option_type, 0) + 1
                if o.strike == 0:
                    zero_strike += 1
            logger.warning(
                f"No matching options for {option_type} strike={target_strike}: "
                f"total={len(options)}, by_type={type_counts}, zero_strike={zero_strike}"
            )
            if zero_strike > 0:
                samples = [f"{o.epic} '{o.instrument_name}'" for o in options if o.strike == 0][:3]
                logger.warning(f"  Unparsed samples: {samples}")
            return None
        best = min(matching, key=lambda o: abs(o.strike - target_strike))
        # Reject if matched strike is too far from target
        if target_strike > 0:
            deviation_pct = abs(best.strike - target_strike) / target_strike * 100
            if deviation_pct > self.MAX_STRIKE_DEVIATION_PCT:
                logger.warning(
                    f"Closest strike {best.strike} is {deviation_pct:.1f}% from "
                    f"target {target_strike} (max {self.MAX_STRIKE_DEVIATION_PCT}%) — rejecting"
                )
                return None
        return best

    def _check_drawdown_gate(self):
        """Check fund-level drawdown breaker. Returns DrawdownDecision or None."""
        try:
            from risk.drawdown_breaker import check_drawdown, DrawdownAction
            decision = check_drawdown()
            if decision.action == DrawdownAction.WARN:
                logger.warning(
                    f"Drawdown WARNING: {decision.reason} "
                    f"(daily={decision.daily_drawdown_pct:.2f}%, "
                    f"weekly={decision.weekly_drawdown_pct:.2f}%)"
                )
            return decision
        except Exception as exc:
            logger.warning(f"Drawdown check failed (allowing trade): {exc}")
            return None
