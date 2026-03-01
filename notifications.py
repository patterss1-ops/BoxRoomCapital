"""
Notification system — Telegram alerts for trade signals, entries, exits, errors.

Setup:
  1. Create a Telegram bot via @BotFather → get TOKEN
  2. Send /start to your bot → get CHAT_ID via https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Set in .env:
     TELEGRAM_TOKEN=your_bot_token
     TELEGRAM_CHAT_ID=your_chat_id
     NOTIFICATIONS_ENABLED=true
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Send alerts via Telegram (and optionally email)."""

    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true"

        if self.enabled and self.telegram_token and self.telegram_chat_id:
            logger.info("Notifications: Telegram enabled")
        elif self.enabled:
            logger.warning("Notifications enabled but TELEGRAM_TOKEN/CHAT_ID not set")
            self.enabled = False
        else:
            logger.info("Notifications: disabled")

    def send(self, message: str, icon: str = "") -> bool:
        """Send a notification. Returns True if sent successfully."""
        if not self.enabled:
            return False
        return self._send_telegram(f"{icon} {message}" if icon else message)

    def signal_detected(self, ticker: str, action: str, reason: str):
        """Alert: strategy signal fired."""
        self.send(f"{ticker} — {action}\n{reason}", icon="📊")

    def trade_entered(self, ticker: str, short_strike: float, long_strike: float,
                      size: float, premium: float, max_loss: float):
        """Alert: trade placed on IG."""
        self.send(
            f"{ticker} — SPREAD OPENED\n"
            f"Short: {short_strike:.0f}, Long: {long_strike:.0f}\n"
            f"Size: £{size}/pt, Premium: {premium:.1f}pts\n"
            f"Max loss: £{max_loss:.0f}",
            icon="🟢",
        )

    def trade_closed(self, ticker: str, pnl: float, reason: str):
        """Alert: trade closed."""
        icon = "✅" if pnl >= 0 else "🔴"
        self.send(
            f"{ticker} — SPREAD CLOSED\n"
            f"P&L: £{pnl:+.2f}\n"
            f"Reason: {reason}",
            icon=icon,
        )

    def trade_rejected(self, ticker: str, reason: str):
        """Alert: trade blocked by safety controller."""
        self.send(f"{ticker} — BLOCKED\n{reason}", icon="⛔")

    def shadow_trade(self, ticker: str, action: str, short_strike: float,
                     long_strike: float, reason: str):
        """Alert: shadow mode trade (not executed)."""
        self.send(
            f"[SHADOW] {ticker} — {action}\n"
            f"Short: {short_strike:.0f}, Long: {long_strike:.0f}\n"
            f"{reason}",
            icon="👻",
        )

    def kill_switch_triggered(self, reason: str):
        """Alert: kill switch activated."""
        self.send(
            f"KILL SWITCH TRIGGERED\n"
            f"{reason}\n"
            f"All trading halted. Manual restart required.",
            icon="🚨",
        )

    def heartbeat(self, open_spreads: int, equity: float, daily_pnl: float,
                  mode: str):
        """Periodic alive check."""
        self.send(
            f"Bot alive — {mode.upper()} mode\n"
            f"Spreads: {open_spreads} open\n"
            f"Equity: £{equity:,.0f}\n"
            f"Today: £{daily_pnl:+,.0f}",
            icon="💓",
        )

    def error(self, message: str):
        """Alert: something went wrong."""
        self.send(f"ERROR: {message}", icon="⚠️")

    # ─── Pipeline / dispatch / reconciliation alerts (D-004) ──────────

    def format_dispatch_summary(self, summary) -> str:
        """Format a DispatchRunSummary into a human-readable message."""
        d = summary.to_dict()
        lines = [
            "DISPATCH CYCLE",
            f"  {d['discovered']} discovered, {d['processed']} processed",
            f"  {d['completed']} completed, {d['retried']} retried, {d['failed']} failed",
        ]
        if d.get("errors", 0) > 0:
            lines.append(f"  {d['errors']} errors")
        if d.get("claim_conflicts", 0) > 0:
            lines.append(f"  {d['claim_conflicts']} claim conflicts")
        return "\n".join(lines)

    def dispatch_alert(self, summary) -> bool:
        """Send a dispatch cycle summary alert."""
        msg = self.format_dispatch_summary(summary)
        icon = "✅" if summary.failed == 0 and summary.errors == 0 else "⚠️"
        return self.send(msg, icon=icon)

    def format_pipeline_errors(self, result) -> Optional[str]:
        """Format orchestration errors into a message, or None if clean."""
        if not result.errors:
            return None
        n = len(result.errors)
        lines = [f"PIPELINE RUN {result.run_id}: {n} error{'s' if n != 1 else ''}"]
        for err in result.errors[:5]:
            lines.append(
                f"  {err.get('strategy_id', '?')}/{err.get('ticker', '?')}: "
                f"{err.get('error', 'unknown')}"
            )
        if n > 5:
            lines.append(f"  ... and {n - 5} more")
        return "\n".join(lines)

    def pipeline_error_alert(self, result) -> bool:
        """Send an alert if the orchestration had errors."""
        msg = self.format_pipeline_errors(result)
        if msg is None:
            return False
        return self.send(msg, icon="🔴")

    def format_reconciliation_summary(self, summary) -> str:
        """Format a ReconcileSummary into a human-readable message."""
        d = summary.to_dict()
        return (
            f"RECONCILIATION — {d['broker']}\n"
            f"  Account: {d['account_id']}\n"
            f"  {d['positions_synced']} positions synced "
            f"(+{d['positions_inserted']} new, "
            f"~{d['positions_updated']} updated, "
            f"-{d['positions_removed']} removed)\n"
            f"  Cash: {d['cash_balance']:,.2f}\n"
            f"  Net liquidation: {d['net_liquidation']:,.2f}"
        )

    def reconciliation_alert(self, summary) -> bool:
        """Send a reconciliation summary alert."""
        msg = self.format_reconciliation_summary(summary)
        return self.send(msg, icon="🔄")

    def _send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
            }
            r = requests.post(url, json=payload, timeout=5)
            if r.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram send failed: {r.status_code} — {r.text[:100]}")
                return False
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False


# Global singleton — import and use: from notifications import notifier
notifier = NotificationHandler()
