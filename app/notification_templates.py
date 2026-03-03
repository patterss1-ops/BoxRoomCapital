"""Notification template engine — templated rendering for trading alerts.

L-005: Provides variable-substitution templates for common trading notifications
with channel-aware formatting (Telegram, email, log) and severity levels.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class NotificationSeverity(str, Enum):
    """Severity level for notifications."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    ALERT = "ALERT"


class NotificationChannel(str, Enum):
    """Delivery channel for notifications."""
    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"
    LOG = "LOG"


@dataclass
class NotificationTemplate:
    """A notification template with variable placeholders."""

    name: str
    subject_template: str
    body_template: str
    severity: NotificationSeverity
    channels: list[NotificationChannel] = field(default_factory=list)
    required_vars: list[str] = field(default_factory=list)


@dataclass
class RenderedNotification:
    """A fully rendered notification ready for delivery."""

    template_name: str
    subject: str
    body: str
    severity: NotificationSeverity
    channel: NotificationChannel
    rendered_at: str = ""

    def __post_init__(self) -> None:
        if not self.rendered_at:
            self.rendered_at = datetime.now(timezone.utc).isoformat()


# Regex to find {variable_name} placeholders
_VAR_PATTERN = re.compile(r"\{(\w+)\}")


class NotificationTemplateEngine:
    """Renders notification templates with variable substitution and channel formatting."""

    def __init__(self) -> None:
        self._templates: dict[str, NotificationTemplate] = {}
        self._register_builtins()

    # ─── Public API ────────────────────────────────────────────────────

    def register(self, template: NotificationTemplate) -> None:
        """Register a custom template. Raises ValueError if name already taken."""
        if template.name in self._templates:
            raise ValueError(f"Template '{template.name}' is already registered")
        self._templates[template.name] = template

    def get_template(self, name: str) -> NotificationTemplate | None:
        """Look up a template by name."""
        return self._templates.get(name)

    def list_templates(self) -> list[str]:
        """Return sorted list of registered template names."""
        return sorted(self._templates.keys())

    def validate(self, template_name: str, variables: dict) -> list[str]:
        """Return list of missing required variables for a template.

        Raises ValueError if the template does not exist.
        """
        tpl = self._templates.get(template_name)
        if tpl is None:
            raise ValueError(f"Unknown template: '{template_name}'")
        return [v for v in tpl.required_vars if v not in variables]

    def render(
        self,
        template_name: str,
        variables: dict,
        channel: NotificationChannel | None = None,
    ) -> list[RenderedNotification]:
        """Render a template for its target channels (or a specific one).

        Returns one RenderedNotification per channel.
        Raises ValueError if the template is unknown or required variables are missing.
        """
        tpl = self._templates.get(template_name)
        if tpl is None:
            raise ValueError(f"Unknown template: '{template_name}'")

        missing = self.validate(template_name, variables)
        if missing:
            raise ValueError(
                f"Missing required variables for '{template_name}': {', '.join(missing)}"
            )

        subject = self._substitute(tpl.subject_template, variables)
        body = self._substitute(tpl.body_template, variables)

        channels = [channel] if channel is not None else tpl.channels
        now = datetime.now(timezone.utc).isoformat()

        results: list[RenderedNotification] = []
        for ch in channels:
            fmt_subject, fmt_body = self._format_for_channel(
                ch, subject, body, tpl.severity,
            )
            results.append(RenderedNotification(
                template_name=template_name,
                subject=fmt_subject,
                body=fmt_body,
                severity=tpl.severity,
                channel=ch,
                rendered_at=now,
            ))
        return results

    def render_bulk(
        self,
        items: list[tuple[str, dict]],
    ) -> list[RenderedNotification]:
        """Render multiple notifications in one call.

        Each item is (template_name, variables). All target channels are used.
        """
        results: list[RenderedNotification] = []
        for template_name, variables in items:
            results.extend(self.render(template_name, variables))
        return results

    # ─── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _substitute(template_str: str, variables: dict) -> str:
        """Replace {var} placeholders with values from variables dict."""
        def _replacer(match: re.Match) -> str:
            key = match.group(1)
            if key in variables:
                return str(variables[key])
            return match.group(0)  # leave unresolved placeholders as-is

        return _VAR_PATTERN.sub(_replacer, template_str)

    @staticmethod
    def _format_for_channel(
        channel: NotificationChannel,
        subject: str,
        body: str,
        severity: NotificationSeverity,
    ) -> tuple[str, str]:
        """Apply channel-specific formatting."""
        if channel == NotificationChannel.TELEGRAM:
            return f"*{subject}*", body
        elif channel == NotificationChannel.EMAIL:
            return subject, f"<p>{body}</p>"
        elif channel == NotificationChannel.LOG:
            combined = f"[{severity.value}] {subject}: {body}"
            return subject, combined
        return subject, body

    def _register_builtins(self) -> None:
        """Register the standard set of trading notification templates."""
        all_channels = [
            NotificationChannel.TELEGRAM,
            NotificationChannel.EMAIL,
            NotificationChannel.LOG,
        ]

        builtins = [
            NotificationTemplate(
                name="trade_executed",
                subject_template="Trade Executed: {side} {qty} {ticker} @ {price}",
                body_template="Executed {side} order for {qty} shares of {ticker} at ${price}.",
                severity=NotificationSeverity.INFO,
                channels=list(all_channels),
                required_vars=["side", "qty", "ticker", "price"],
            ),
            NotificationTemplate(
                name="risk_alert",
                subject_template="Risk Alert: {alert_type}",
                body_template="Risk Alert: {alert_type} — {message}",
                severity=NotificationSeverity.CRITICAL,
                channels=list(all_channels),
                required_vars=["alert_type", "message"],
            ),
            NotificationTemplate(
                name="drawdown_warning",
                subject_template="Drawdown Warning: {strategy}",
                body_template="Drawdown Warning: {strategy} at {drawdown_pct}% drawdown.",
                severity=NotificationSeverity.WARNING,
                channels=list(all_channels),
                required_vars=["strategy", "drawdown_pct"],
            ),
            NotificationTemplate(
                name="signal_generated",
                subject_template="Signal: {verdict} on {ticker}",
                body_template="Signal: {verdict} on {ticker} (score: {score}).",
                severity=NotificationSeverity.INFO,
                channels=list(all_channels),
                required_vars=["verdict", "ticker", "score"],
            ),
            NotificationTemplate(
                name="system_health",
                subject_template="System Health: {component}",
                body_template="System Health: {component} is {status}.",
                severity=NotificationSeverity.WARNING,
                channels=list(all_channels),
                required_vars=["component", "status"],
            ),
            NotificationTemplate(
                name="rebalance_triggered",
                subject_template="Rebalance: {strategy}",
                body_template="Rebalance: {strategy} drift {drift_pct}% exceeds threshold.",
                severity=NotificationSeverity.ALERT,
                channels=list(all_channels),
                required_vars=["strategy", "drift_pct"],
            ),
        ]

        for tpl in builtins:
            self._templates[tpl.name] = tpl
