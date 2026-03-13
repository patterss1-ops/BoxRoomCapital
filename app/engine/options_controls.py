"""Operator controls for OptionsBot (kill switch, throttle, cooldowns, manual actions)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import config
from data.trade_db import (
    log_event, log_control_action,
    get_open_option_positions,
)

logger = logging.getLogger(__name__)


class OptionsControlsMixin:

    def request_stop(self):
        self.running = False

    def set_paused(self, paused: bool) -> str:
        with self._control_lock:
            self.paused = paused
            state = "paused" if paused else "resumed"
            detail = "Scheduled scans disabled" if paused else "Scheduled scans enabled"
            log_event("POSITION", f"Options bot {state}", detail, strategy="IBS Credit Spreads")
            logger.info(f"Control action: bot {state}")
            return f"Bot {state}."

    def get_override_status(self) -> dict:
        with self._control_lock:
            self._clear_expired_cooldowns()
            return {
                "kill_switch_active": self.kill_switch_active,
                "kill_switch_reason": self.kill_switch_reason,
                "risk_throttle_pct": self.risk_throttle_pct,
                "cooldowns": {
                    ticker: until.isoformat()
                    for ticker, until in sorted(self.market_cooldowns.items())
                },
                "cooldowns_count": len(self.market_cooldowns),
            }

    def set_kill_switch(self, active: bool, reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            self.kill_switch_active = active
            if active:
                self.kill_switch_reason = reason.strip() or "Manual operator kill switch"
            else:
                self.kill_switch_reason = ""
            self._persist_control_state()

            state = "ENABLED" if active else "DISABLED"
            detail = self.kill_switch_reason if active else (reason.strip() or "Manual clear")
            log_control_action(
                action="kill_switch",
                value=state,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Kill switch {state}", detail, strategy="IBS Credit Spreads")
            logger.warning(f"Control action: kill switch {state} ({detail})")
            return f"Kill switch {state.lower()}."

    def set_risk_throttle(self, pct: float, reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            clamped = min(1.0, max(0.1, float(pct)))
            self.risk_throttle_pct = clamped
            self._persist_control_state()

            pct_text = f"{clamped * 100:.0f}%"
            detail = reason.strip() or "Operator override"
            log_control_action(
                action="risk_throttle",
                value=pct_text,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Risk throttle set to {pct_text}", detail, strategy="IBS Credit Spreads")
            logger.info(f"Control action: risk throttle {pct_text} ({detail})")
            return f"Risk throttle set to {pct_text}."

    def set_market_cooldown(self, ticker: str, minutes: int,
                            reason: str = "", actor: str = "operator") -> str:
        from app.engine.options_bot import UK
        with self._control_lock:
            clean_ticker = str(ticker or "").upper().strip()
            if not clean_ticker:
                return "Ticker is required."
            duration = max(1, int(minutes))
            until = datetime.now(UK) + timedelta(minutes=duration)
            self.market_cooldowns[clean_ticker] = until
            self._persist_control_state()

            detail = reason.strip() or "Operator cooldown"
            value = f"{clean_ticker} until {until.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            log_control_action(
                action="market_cooldown_set",
                value=value,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Cooldown set for {clean_ticker}",
                      f"{duration}m ({detail})", ticker=clean_ticker, strategy="IBS Credit Spreads")
            logger.info(f"Control action: cooldown set {clean_ticker} for {duration}m")
            return f"Cooldown set for {clean_ticker} ({duration}m)."

    def clear_market_cooldown(self, ticker: str, reason: str = "", actor: str = "operator") -> str:
        with self._control_lock:
            clean_ticker = str(ticker or "").upper().strip()
            if not clean_ticker:
                return "Ticker is required."
            existed = clean_ticker in self.market_cooldowns
            self.market_cooldowns.pop(clean_ticker, None)
            self._persist_control_state()

            detail = reason.strip() or "Operator cooldown clear"
            log_control_action(
                action="market_cooldown_clear",
                value=clean_ticker,
                reason=detail,
                actor=actor,
            )
            log_event("POSITION", f"Cooldown cleared for {clean_ticker}",
                      detail, ticker=clean_ticker, strategy="IBS Credit Spreads")
            logger.info(f"Control action: cooldown cleared {clean_ticker}")
            if existed:
                return f"Cooldown cleared for {clean_ticker}."
            return f"No active cooldown for {clean_ticker}."

    def run_manual_scan(self) -> dict:
        with self._control_lock:
            logger.info("Control action: running manual signal scan now")
            log_event("SCAN", "Manual scan requested", "Triggered from control plane")
            self._run_all_signals()
            return {
                "ok": True,
                "message": "Manual signal scan completed.",
                "tickers": config.LIVE_TRADING_TICKERS,
            }

    def build_reconcile_report(self) -> dict:
        from app.engine.options_bot import UK
        with self._control_lock:
            db_positions = get_open_option_positions()
            memory_positions = list(self.open_spreads.values())

            db_spread_ids = set(str(p.get("spread_id", "")) for p in db_positions if p.get("spread_id"))
            mem_spread_ids = set(str(p.get("spread_id", "")) for p in memory_positions if p.get("spread_id"))
            db_only_spread_ids = sorted(db_spread_ids - mem_spread_ids)
            memory_only_spread_ids = sorted(mem_spread_ids - db_spread_ids)

            db_deal_ids = set()
            for pos in db_positions:
                short_id = str(pos.get("short_deal_id", "") or "").strip()
                long_id = str(pos.get("long_deal_id", "") or "").strip()
                if short_id:
                    db_deal_ids.add(short_id)
                if long_id:
                    db_deal_ids.add(long_id)

            broker_deal_ids = set()
            broker_error = ""
            if self.broker:
                try:
                    for broker_pos in self.broker.get_positions():
                        deal_id = str(getattr(broker_pos, "deal_id", "") or "").strip()
                        if deal_id:
                            broker_deal_ids.add(deal_id)
                except Exception as exc:
                    broker_error = str(exc)

            db_only_deal_ids = sorted(db_deal_ids - broker_deal_ids)
            broker_only_deal_ids = sorted(broker_deal_ids - db_deal_ids)

            suggestions = []
            if db_only_spread_ids:
                suggestions.append(
                    "Sync runtime state from DB (manual reconcile) before taking manual close actions."
                )
            if memory_only_spread_ids:
                suggestions.append(
                    "In-memory spreads not present in DB; review recent crash and persist/clear those entries."
                )
            if db_only_deal_ids:
                suggestions.append(
                    "DB contains deal IDs absent at broker; likely stale records. Investigate and close stale spreads."
                )
            if broker_only_deal_ids:
                suggestions.append(
                    "Broker has unmanaged deal IDs; import them into DB or close manually in IG."
                )
            if not suggestions:
                suggestions.append("No mismatches detected.")

            return {
                "db_open_spreads": len(db_spread_ids),
                "memory_open_spreads": len(mem_spread_ids),
                "broker_open_deals": len(broker_deal_ids),
                "db_only_spread_ids": db_only_spread_ids,
                "memory_only_spread_ids": memory_only_spread_ids,
                "db_only_deal_ids": db_only_deal_ids,
                "broker_only_deal_ids": broker_only_deal_ids,
                "has_mismatch": bool(
                    db_only_spread_ids or memory_only_spread_ids or db_only_deal_ids or broker_only_deal_ids
                ),
                "broker_error": broker_error,
                "suggestions": suggestions,
                "generated_at": datetime.now(UK).isoformat(),
            }

    def reconcile_now(self) -> dict:
        with self._control_lock:
            db_positions = get_open_option_positions()
            previous_ids = set(self.open_spreads.keys())

            self.open_spreads = {p["spread_id"]: p for p in db_positions}
            current_ids = set(self.open_spreads.keys())

            if self.broker:
                acct = self.broker.get_account_info()
                if acct.equity > 0:
                    self.safety.equity = acct.equity

            # Auto-close DB spreads whose deal IDs are gone from broker
            auto_closed = []
            if self.broker:
                broker_deal_ids = set()
                try:
                    for broker_pos in self.broker.get_positions():
                        deal_id = str(getattr(broker_pos, "deal_id", "") or "").strip()
                        if deal_id:
                            broker_deal_ids.add(deal_id)
                except Exception as exc:
                    logger.warning(f"Reconcile: broker position fetch failed: {exc}")
                    broker_deal_ids = None  # Can't reconcile without broker data

                if broker_deal_ids is not None:
                    for spread_id, spread in list(self.open_spreads.items()):
                        short_id = str(spread.get("short_deal_id", "") or "").strip()
                        long_id = str(spread.get("long_deal_id", "") or "").strip()
                        # If BOTH deal IDs are missing from broker, position was closed externally
                        short_gone = short_id and short_id not in broker_deal_ids
                        long_gone = long_id and long_id not in broker_deal_ids
                        if short_gone and long_gone:
                            ticker = spread.get("ticker", "?")
                            logger.info(
                                f"  Reconcile: {ticker} spread {spread_id} closed externally "
                                f"(deals {short_id}, {long_id} absent from broker)"
                            )
                            close_option_position(spread_id, exit_pnl=0.0,
                                                  exit_reason="Closed externally (reconcile)")
                            self.open_spreads.pop(spread_id, None)
                            auto_closed.append(spread_id)
                            log_event("POSITION",
                                      f"{ticker} — Closed externally (auto-reconcile)",
                                      f"spread_id={spread_id}",
                                      ticker=ticker, strategy="IBS Credit Spreads")

            current_ids = set(self.open_spreads.keys())
            self.safety.update_state(self.safety.equity, list(self.open_spreads.values()))
            added = sorted(current_ids - previous_ids)
            removed = sorted(previous_ids - current_ids)
            report = self.build_reconcile_report()
            detail = (
                f"added={len(added)} removed={len(removed)} open={len(current_ids)} "
                f"auto_closed={len(auto_closed)} "
                f"db_only_deals={len(report['db_only_deal_ids'])} "
                f"broker_only_deals={len(report['broker_only_deal_ids'])}"
            )
            logger.info(f"Control action: reconcile complete ({detail})")
            if report["broker_error"]:
                log_event(
                    "ERROR",
                    "Manual reconcile broker fetch failed",
                    report["broker_error"],
                    strategy="IBS Credit Spreads",
                )
            if report["has_mismatch"]:
                log_event(
                    "ERROR",
                    "Manual reconcile found DB/Broker mismatches",
                    detail,
                    strategy="IBS Credit Spreads",
                )
            else:
                log_event("POSITION", "Manual reconcile complete", detail, strategy="IBS Credit Spreads")
            return {
                "ok": True,
                "message": f"Reconcile complete ({detail}).",
                "added": added,
                "removed": removed,
                "auto_closed": auto_closed,
                "open_count": len(current_ids),
                "db_only_deal_ids": report["db_only_deal_ids"],
                "broker_only_deal_ids": report["broker_only_deal_ids"],
                "mismatch": report["has_mismatch"],
                "report": report,
            }

    def close_spread_manual(self, spread_id: str = "", ticker: str = "",
                            reason: str = "Manual close from control plane") -> dict:
        with self._control_lock:
            spread = None
            if spread_id:
                spread = self.open_spreads.get(spread_id)
            elif ticker:
                spread = self._get_open_spread_dict(ticker)

            if not spread:
                return {"ok": False, "message": "No matching open spread found."}

            ok = self._close_spread(spread, reason)
            if not ok:
                return {"ok": False, "message": "Spread close attempt failed; see logs."}
            return {
                "ok": True,
                "message": f"Closed spread {spread.get('spread_id', '')} ({spread.get('ticker', '')}).",
            }

    def _clear_expired_cooldowns(self):
        from app.engine.options_bot import UK
        now = datetime.now(UK)
        expired = [ticker for ticker, until in self.market_cooldowns.items() if until <= now]
        for ticker in expired:
            self.market_cooldowns.pop(ticker, None)
        if expired:
            self._persist_control_state()

    def _cooldown_remaining(self, ticker: str) -> Optional[timedelta]:
        from app.engine.options_bot import UK
        self._clear_expired_cooldowns()
        until = self.market_cooldowns.get(str(ticker).upper())
        if not until:
            return None
        now = datetime.now(UK)
        remaining = until - now
        return remaining if remaining.total_seconds() > 0 else None
