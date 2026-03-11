"""Shared fragment context loaders for cached operator surfaces."""
from __future__ import annotations

from typing import Any, Callable


def _get_ledger_fragment_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    ledger_cache_ttl_seconds: float,
    get_unified_ledger_snapshot: Callable[..., Any],
    get_ledger_reconcile_report: Callable[..., Any],
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        return {
            "ledger": get_unified_ledger_snapshot(nav_limit=25),
            "reconcile": get_ledger_reconcile_report(stale_after_minutes=30),
        }

    return get_cached_value(
        "ledger-fragment",
        ledger_cache_ttl_seconds,
        _load,
        stale_on_error=True,
    )


def _get_risk_briefing_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    risk_briefing_cache_ttl_seconds: float,
    build_risk_briefing_payload: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return get_cached_value(
        "risk-briefing",
        risk_briefing_cache_ttl_seconds,
        build_risk_briefing_payload,
        stale_on_error=True,
    )


def _get_intelligence_feed_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    intelligence_feed_cache_ttl_seconds: float,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        from datetime import datetime, timezone as tz

        macro_regime = ""
        try:
            from intelligence.feature_store import FeatureStore
            from intelligence.macro_regime import MacroRegimeClassifier

            feature_store = FeatureStore()
            try:
                result = MacroRegimeClassifier(feature_store=feature_store).classify()
                macro_regime = result.regime.value if result else ""
            finally:
                feature_store.close()
        except Exception:
            pass

        layers = []
        try:
            from app.signal.types import LayerId
            from intelligence.event_store import EventStore

            event_store = EventStore()
            try:
                for layer_id in LayerId:
                    latest = event_store.get_latest_by_layer(layer_id.value)
                    fresh = latest is not None
                    layers.append({"id": layer_id.value, "fresh": fresh, "stale": False})
            except Exception:
                pass
            finally:
                event_store.close()
        except Exception:
            pass

        return {
            "as_of": datetime.now(tz.utc).isoformat(),
            "macro_regime": macro_regime,
            "layers": layers,
            "candidates": [],
            "ai_verdicts": {},
        }

    return get_cached_value(
        "intelligence-feed",
        intelligence_feed_cache_ttl_seconds,
        _load,
        stale_on_error=True,
    )


def _get_portfolio_analytics_context(
    days: int,
    *,
    max_days: int,
    get_cached_value: Callable[..., dict[str, Any]],
    portfolio_analytics_cache_ttl_seconds: float,
    build_portfolio_analytics_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    bounded_days = max(7, min(int(days), int(max_days)))
    return get_cached_value(
        f"portfolio-analytics:{bounded_days}",
        portfolio_analytics_cache_ttl_seconds,
        lambda: build_portfolio_analytics_payload(days=bounded_days),
        stale_on_error=True,
    )
