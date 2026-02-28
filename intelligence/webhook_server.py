"""TradingView webhook parsing and auth helpers."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional

DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024


class WebhookValidationError(ValueError):
    """Raised when a webhook request fails auth or payload validation."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class TradingViewSignal:
    """Normalized TradingView signal payload."""

    ticker: str
    action: str
    strategy: str
    timeframe: str


def parse_json_payload(raw_body: bytes, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> dict[str, Any]:
    """Parse and validate a TradingView JSON payload."""
    if not raw_body:
        raise WebhookValidationError("invalid_payload", "empty webhook payload", 400)
    if len(raw_body) > max_payload_bytes:
        raise WebhookValidationError("payload_too_large", "webhook payload too large", 413)

    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WebhookValidationError("invalid_payload", f"invalid UTF-8 payload: {exc}", 400) from exc

    text = decoded.strip()
    if not text:
        raise WebhookValidationError("invalid_payload", "empty webhook payload", 400)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WebhookValidationError("invalid_payload", f"invalid JSON payload: {exc.msg}", 400) from exc

    if not isinstance(payload, dict):
        raise WebhookValidationError("invalid_payload", "payload must be a JSON object", 400)
    return payload


def extract_auth_token(payload: dict[str, Any], header_token: str = "", query_token: str = "") -> str:
    """Resolve webhook token from header, query, or payload."""
    if header_token and header_token.strip():
        return header_token.strip()
    if query_token and query_token.strip():
        return query_token.strip()

    for key in ("token", "auth_token", "webhook_token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def validate_expected_token(expected_token: str, provided_token: str) -> None:
    """Validate expected token against provided token."""
    if not expected_token.strip():
        raise WebhookValidationError(
            "webhook_not_configured",
            "TradingView webhook token is not configured.",
            503,
        )
    if not provided_token:
        raise WebhookValidationError("missing_token", "missing webhook token", 401)
    if expected_token.strip() != provided_token:
        raise WebhookValidationError("invalid_token", "invalid webhook token", 401)


def summarize_payload(payload: dict[str, Any]) -> TradingViewSignal:
    """Extract a normalized signal summary from payload fields."""
    ticker = str(payload.get("symbol") or payload.get("ticker") or "").strip().upper()
    action = str(payload.get("action") or payload.get("side") or payload.get("signal") or "").strip().lower()
    strategy = str(payload.get("strategy") or payload.get("strategy_id") or "tradingview").strip()
    timeframe = str(payload.get("timeframe") or payload.get("interval") or "").strip()
    return TradingViewSignal(
        ticker=ticker,
        action=action,
        strategy=strategy,
        timeframe=timeframe,
    )


def build_audit_detail(reason: str, client_ip: str, payload: Optional[dict[str, Any]] = None) -> str:
    """Build compact audit JSON for webhook accepts/rejections."""
    payload = payload or {}
    detail = {
        "reason": reason,
        "client_ip": client_ip,
        "symbol": payload.get("symbol") or payload.get("ticker"),
        "action": payload.get("action") or payload.get("side") or payload.get("signal"),
        "strategy": payload.get("strategy") or payload.get("strategy_id"),
    }
    return json.dumps(detail, sort_keys=True, default=str)
