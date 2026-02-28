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
