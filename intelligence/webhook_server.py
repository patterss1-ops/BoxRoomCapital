"""TradingView webhook parsing, auth, and normalization helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hmac
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


@dataclass(frozen=True)
class TradingViewStrategySpec:
    """Execution + validation policy for one TradingView-enabled strategy slot."""

    strategy_id: str
    strategy_version: str
    sleeve: str
    account_type: str
    broker_target: str
    base_qty: float
    risk_tags: tuple[str, ...]
    requirements: dict[str, Any]
    allowed_tickers: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    timeframe: str


@dataclass(frozen=True)
class NormalizedTradingViewAlert:
    """Normalized TradingView webhook payload with repo-level policy applied."""

    schema_version: str
    alert_id: str
    strategy_id: str
    ticker: str
    action: str
    timeframe: str
    event_timestamp: str
    signal_price: Optional[float] = None
    indicators: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def source_ref(self) -> str:
        return f"tv://{self.strategy_id}/{self.alert_id}"

    @property
    def correlation_id(self) -> str:
        return f"tradingview:{self.strategy_id}:{self.ticker}:{self.alert_id}"


_TRADINGVIEW_SLOT_POLICY: dict[str, dict[str, Any]] = {
    "ibs_spreadbet_long": {
        "allowed_actions": ("buy", "sell"),
        "timeframe": "1D",
    },
    "ibs_spreadbet_short": {
        "allowed_actions": ("short", "cover"),
        "timeframe": "1D",
    },
}


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
    expected = expected_token.strip()
    if not expected:
        raise WebhookValidationError(
            "webhook_not_configured",
            "TradingView webhook token is not configured.",
            503,
        )
    if not provided_token:
        raise WebhookValidationError("missing_token", "missing webhook token", 401)
    # Constant-time compare to avoid timing oracle leaks on webhook secrets.
    if not hmac.compare_digest(expected.encode("utf-8"), provided_token.encode("utf-8")):
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


def get_tradingview_strategy_registry(
    slot_configs: Optional[list[dict[str, Any]]] = None,
    enabled_strategies: Optional[list[str]] = None,
) -> dict[str, TradingViewStrategySpec]:
    """Build the TradingView-enabled strategy registry from configured slots."""
    import config

    slots = slot_configs if slot_configs is not None else list(getattr(config, "STRATEGY_SLOTS", []))
    if enabled_strategies is None:
        enabled_strategies = list(getattr(config, "TRADINGVIEW_ENABLED_STRATEGIES", []))

    enabled = {str(item).strip().lower() for item in enabled_strategies if str(item).strip()}
    registry: dict[str, TradingViewStrategySpec] = {}

    for slot in slots:
        slot_id = str(slot.get("id") or "").strip().lower()
        if not slot_id or slot_id not in _TRADINGVIEW_SLOT_POLICY:
            continue
        if enabled and slot_id not in enabled:
            continue
        if not slot.get("enabled", True):
            continue

        policy = _TRADINGVIEW_SLOT_POLICY[slot_id]
        registry[slot_id] = TradingViewStrategySpec(
            strategy_id=slot_id,
            strategy_version=str(slot.get("strategy_version") or "1"),
            sleeve=str(slot.get("sleeve") or ""),
            account_type=str(slot.get("account_type") or ""),
            broker_target=str(slot.get("broker_target") or ""),
            base_qty=float(slot.get("base_qty") or 1.0),
            risk_tags=tuple(str(tag).strip() for tag in slot.get("risk_tags", []) if str(tag).strip()),
            requirements=dict(slot.get("requirements") or {}),
            allowed_tickers=tuple(str(t).strip().upper() for t in slot.get("tickers", []) if str(t).strip()),
            allowed_actions=tuple(str(a).strip().lower() for a in policy["allowed_actions"]),
            timeframe=str(policy["timeframe"]),
        )
    return registry


def normalize_tradingview_alert(
    payload: dict[str, Any],
    registry: dict[str, TradingViewStrategySpec],
    max_age_seconds: int = 600,
    now_utc: Optional[datetime] = None,
) -> NormalizedTradingViewAlert:
    """Validate and normalize a TradingView payload against repo policy."""
    signal = summarize_payload(payload)
    schema_version = str(payload.get("schema_version") or "legacy").strip() or "legacy"
    strategy_id = str(payload.get("strategy_id") or payload.get("strategy") or signal.strategy or "").strip().lower()
    ticker = str(payload.get("ticker") or payload.get("symbol") or signal.ticker or "").strip().upper()
    action = str(payload.get("action") or payload.get("side") or payload.get("signal") or signal.action or "").strip().lower()
    timeframe = str(payload.get("timeframe") or payload.get("interval") or signal.timeframe or "").strip()

    if not strategy_id:
        raise WebhookValidationError("missing_strategy", "TradingView payload missing strategy_id.", 422)
    spec = registry.get(strategy_id)
    if spec is None:
        raise WebhookValidationError("unknown_strategy", f"TradingView strategy '{strategy_id}' is not registered.", 422)

    if not ticker:
        raise WebhookValidationError("missing_ticker", "TradingView payload missing ticker/symbol field.", 422)
    if ticker not in spec.allowed_tickers:
        raise WebhookValidationError(
            "unsupported_ticker",
            f"Ticker '{ticker}' is not enabled for strategy '{strategy_id}'.",
            422,
        )

    if not action:
        raise WebhookValidationError("missing_action", "TradingView payload missing action field.", 422)
    if action not in spec.allowed_actions:
        raise WebhookValidationError(
            "unsupported_action",
            f"Action '{action}' is not allowed for strategy '{strategy_id}'.",
            422,
        )

    clean_timeframe = timeframe or spec.timeframe
    if clean_timeframe != spec.timeframe:
        raise WebhookValidationError(
            "unsupported_timeframe",
            f"Strategy '{strategy_id}' expects timeframe '{spec.timeframe}', got '{clean_timeframe}'.",
            422,
        )

    if schema_version == "tv.v1":
        alert_id = str(payload.get("alert_id") or "").strip()
        if not alert_id:
            raise WebhookValidationError("missing_alert_id", "TradingView payload missing alert_id.", 422)
        event_timestamp = str(payload.get("event_timestamp") or payload.get("timestamp") or "").strip()
        if not event_timestamp:
            raise WebhookValidationError("missing_event_timestamp", "TradingView payload missing event_timestamp.", 422)
    else:
        alert_id = str(payload.get("alert_id") or f"{strategy_id}:{ticker}:{action}:{clean_timeframe}").strip()
        event_timestamp = str(payload.get("event_timestamp") or payload.get("timestamp") or "").strip()
        if not event_timestamp:
            event_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    parsed_event_dt = _parse_iso8601(event_timestamp)
    if max_age_seconds > 0:
        now = now_utc or datetime.now(timezone.utc)
        age_seconds = (now.astimezone(timezone.utc) - parsed_event_dt).total_seconds()
        if age_seconds < -5:
            raise WebhookValidationError("future_timestamp", "TradingView event_timestamp is in the future.", 422)
        if age_seconds > max_age_seconds:
            raise WebhookValidationError(
                "stale_signal",
                f"TradingView signal is too old ({int(age_seconds)}s > {int(max_age_seconds)}s).",
                422,
            )

    signal_price = _coerce_float(
        payload.get("signal_price")
        or payload.get("price")
        or payload.get("close")
        or payload.get("last")
    )

    indicators = {
        "ibs": _coerce_float(payload.get("ibs")),
        "rsi2": _coerce_float(payload.get("rsi2") or payload.get("rsi")),
        "ema200": _coerce_float(payload.get("ema200")),
        "vix": _coerce_float(payload.get("vix")),
        "bars_in_trade": _coerce_int(payload.get("bars_in_trade")),
    }

    return NormalizedTradingViewAlert(
        schema_version=schema_version,
        alert_id=alert_id,
        strategy_id=strategy_id,
        ticker=ticker,
        action=action,
        timeframe=clean_timeframe,
        event_timestamp=parsed_event_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        signal_price=signal_price,
        indicators={k: v for k, v in indicators.items() if v is not None},
        raw_payload=dict(payload),
    )


def _parse_iso8601(raw: str) -> datetime:
    candidate = str(raw or "").strip().replace("Z", "+00:00")
    if not candidate:
        raise WebhookValidationError("invalid_timestamp", "TradingView event_timestamp is required.", 422)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise WebhookValidationError("invalid_timestamp", f"Invalid event_timestamp '{raw}'.", 422) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
