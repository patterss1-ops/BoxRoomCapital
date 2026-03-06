"""Unit tests for webhook auth helpers."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence.webhook_server import WebhookValidationError, validate_expected_token


def test_validate_expected_token_accepts_matching_secret():
    validate_expected_token("my-secret", "my-secret")


def test_validate_expected_token_strips_expected_secret():
    validate_expected_token("  my-secret  ", "my-secret")


@pytest.mark.parametrize(
    ("expected", "provided", "code"),
    [
        ("", "foo", "webhook_not_configured"),
        ("token", "", "missing_token"),
        ("token", "wrong", "invalid_token"),
    ],
)
def test_validate_expected_token_rejects_invalid_values(expected: str, provided: str, code: str):
    with pytest.raises(WebhookValidationError) as exc:
        validate_expected_token(expected, provided)
    assert exc.value.code == code


from intelligence.webhook_server import (
    TradingViewStrategySpec,
    parse_json_payload,
    extract_auth_token,
    get_tradingview_strategy_registry,
    normalize_tradingview_alert,
    summarize_payload,
    build_audit_detail,
    TradingViewSignal,
)


class TestParseJsonPayload:
    def test_valid_json(self):
        result = parse_json_payload(b'{"ticker": "SPY"}')
        assert result == {"ticker": "SPY"}

    def test_empty_body(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b"")
        assert exc.value.code == "invalid_payload"
        assert exc.value.status_code == 400

    def test_whitespace_only_body(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b"   ")
        assert exc.value.code == "invalid_payload"

    def test_too_large(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b'{"x": 1}', max_payload_bytes=3)
        assert exc.value.code == "payload_too_large"
        assert exc.value.status_code == 413

    def test_invalid_json(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b"not json at all")
        assert exc.value.code == "invalid_payload"

    def test_array_rejected(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b'[1, 2, 3]')
        assert exc.value.code == "invalid_payload"
        assert "object" in exc.value.message


class TestExtractAuthToken:
    def test_header_token_preferred(self):
        result = extract_auth_token({"token": "payload-tok"}, header_token="header-tok", query_token="query-tok")
        assert result == "header-tok"

    def test_query_token_fallback(self):
        result = extract_auth_token({"token": "payload-tok"}, header_token="", query_token="query-tok")
        assert result == "query-tok"

    def test_payload_token_fallback(self):
        result = extract_auth_token({"token": "payload-tok"}, header_token="", query_token="")
        assert result == "payload-tok"

    def test_payload_auth_token_key(self):
        result = extract_auth_token({"auth_token": "auth-tok"})
        assert result == "auth-tok"

    def test_payload_webhook_token_key(self):
        result = extract_auth_token({"webhook_token": "wh-tok"})
        assert result == "wh-tok"

    def test_no_token_returns_empty(self):
        result = extract_auth_token({})
        assert result == ""

    def test_strips_whitespace(self):
        result = extract_auth_token({}, header_token="  tok  ")
        assert result == "tok"


class TestSummarizePayload:
    def test_standard_fields(self):
        signal = summarize_payload({"symbol": "AAPL", "action": "buy", "strategy": "my_strat", "timeframe": "1h"})
        assert signal.ticker == "AAPL"
        assert signal.action == "buy"
        assert signal.strategy == "my_strat"
        assert signal.timeframe == "1h"

    def test_alternate_field_names(self):
        signal = summarize_payload({"ticker": "msft", "side": "sell", "strategy_id": "algo1", "interval": "4h"})
        assert signal.ticker == "MSFT"
        assert signal.action == "sell"
        assert signal.strategy == "algo1"
        assert signal.timeframe == "4h"

    def test_missing_fields(self):
        signal = summarize_payload({})
        assert signal.ticker == ""
        assert signal.action == ""
        assert signal.strategy == "tradingview"
        assert signal.timeframe == ""


class TestBuildAuditDetail:
    def test_includes_fields(self):
        import json as _json
        detail = build_audit_detail("accepted", "1.2.3.4", {"symbol": "SPY", "action": "buy"})
        parsed = _json.loads(detail)
        assert parsed["reason"] == "accepted"
        assert parsed["client_ip"] == "1.2.3.4"
        assert parsed["symbol"] == "SPY"
        assert parsed["action"] == "buy"

    def test_none_payload(self):
        import json as _json
        detail = build_audit_detail("rejected", "0.0.0.0", None)
        parsed = _json.loads(detail)
        assert parsed["reason"] == "rejected"
        assert parsed["symbol"] is None


class TestTradingViewRegistry:
    def test_registry_reads_supported_slots_only(self):
        registry = get_tradingview_strategy_registry(
            slot_configs=[
                {
                    "id": "ibs_spreadbet_long",
                    "strategy_version": "1.0",
                    "sleeve": "sleeve_1_ibs",
                    "account_type": "SPREADBET",
                    "broker_target": "ig",
                    "base_qty": 1.0,
                    "risk_tags": ["mean_reversion", "ig"],
                    "requirements": {"requires_spreadbet": True},
                    "tickers": ["SPY", "QQQ"],
                    "enabled": True,
                },
                {
                    "id": "gtaa_isa",
                    "strategy_version": "1.0",
                    "sleeve": "rotation",
                    "account_type": "ISA",
                    "broker_target": "ibkr",
                    "base_qty": 1.0,
                    "risk_tags": ["trend"],
                    "requirements": {"requires_spot_etf": True},
                    "tickers": ["SPY"],
                    "enabled": True,
                },
            ],
            enabled_strategies=["ibs_spreadbet_long"],
        )
        assert list(registry.keys()) == ["ibs_spreadbet_long"]
        assert registry["ibs_spreadbet_long"].allowed_actions == ("buy", "sell")
        assert registry["ibs_spreadbet_long"].allowed_tickers == ("SPY", "QQQ")


class TestNormalizeTradingViewAlert:
    def _registry(self) -> dict[str, TradingViewStrategySpec]:
        return {
            "ibs_spreadbet_long": TradingViewStrategySpec(
                strategy_id="ibs_spreadbet_long",
                strategy_version="1.0",
                sleeve="sleeve_1_ibs",
                account_type="SPREADBET",
                broker_target="ig",
                base_qty=1.0,
                risk_tags=("mean_reversion", "ig"),
                requirements={"requires_spreadbet": True},
                allowed_tickers=("SPY", "QQQ"),
                allowed_actions=("buy", "sell"),
                timeframe="1D",
            ),
        }

    def test_tv_v1_payload_normalizes(self):
        alert = normalize_tradingview_alert(
            payload={
                "schema_version": "tv.v1",
                "alert_id": "spy-buy-1",
                "strategy_id": "ibs_spreadbet_long",
                "ticker": "spy",
                "action": "buy",
                "timeframe": "1D",
                "event_timestamp": "2026-03-05T12:00:00Z",
                "signal_price": "500.25",
                "ibs": "0.21",
                "rsi2": "17.4",
            },
            registry=self._registry(),
            max_age_seconds=600,
            now_utc=__import__("datetime").datetime(2026, 3, 5, 12, 5, tzinfo=__import__("datetime").timezone.utc),
        )
        assert alert.strategy_id == "ibs_spreadbet_long"
        assert alert.ticker == "SPY"
        assert alert.action == "buy"
        assert alert.signal_price == 500.25
        assert alert.indicators["ibs"] == 0.21
        assert alert.source_ref == "tv://ibs_spreadbet_long/spy-buy-1"

    def test_rejects_unsupported_ticker(self):
        with pytest.raises(WebhookValidationError) as exc:
            normalize_tradingview_alert(
                payload={
                    "schema_version": "tv.v1",
                    "alert_id": "dia-buy-1",
                    "strategy_id": "ibs_spreadbet_long",
                    "ticker": "DIA",
                    "action": "buy",
                    "timeframe": "1D",
                    "event_timestamp": "2026-03-05T12:00:00Z",
                },
                registry=self._registry(),
                max_age_seconds=600,
                now_utc=__import__("datetime").datetime(2026, 3, 5, 12, 5, tzinfo=__import__("datetime").timezone.utc),
            )
        assert exc.value.code == "unsupported_ticker"

    def test_rejects_stale_signal(self):
        with pytest.raises(WebhookValidationError) as exc:
            normalize_tradingview_alert(
                payload={
                    "schema_version": "tv.v1",
                    "alert_id": "spy-buy-old",
                    "strategy_id": "ibs_spreadbet_long",
                    "ticker": "SPY",
                    "action": "buy",
                    "timeframe": "1D",
                    "event_timestamp": "2026-03-05T11:40:00Z",
                },
                registry=self._registry(),
                max_age_seconds=600,
                now_utc=__import__("datetime").datetime(2026, 3, 5, 12, 5, tzinfo=__import__("datetime").timezone.utc),
            )
        assert exc.value.code == "stale_signal"
