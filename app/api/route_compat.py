"""Runtime compatibility bindings for extracted route modules."""
from __future__ import annotations

import os
import sys
from typing import Any, Callable


class ServerAttributeProxy:
    """Resolve selected route globals from the server module at runtime."""

    def __init__(self, module_name: str, name: str) -> None:
        self._module_name = module_name
        self._name = name

    def _value(self) -> Any:
        return getattr(sys.modules[self._module_name], self._name)

    def __bool__(self) -> bool:
        return bool(self._value())

    def __fspath__(self) -> str:
        return os.fspath(self._value())

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._value(), attr)

    def __repr__(self) -> str:
        return repr(self._value())

    def __str__(self) -> str:
        return str(self._value())


def server_callable(module_name: str, name: str) -> Callable[..., Any]:
    def _call(*args: Any, **kwargs: Any) -> Any:
        target = getattr(sys.modules[module_name], name)
        return target(*args, **kwargs)

    _call.__name__ = name
    return _call


def bind_route_compatibility(
    module_name: str,
    module: Any,
    *,
    callables: tuple[str, ...] = (),
    values: tuple[str, ...] = (),
) -> None:
    """Keep extracted routers compatible with existing server.* monkeypatches."""
    for name in callables:
        setattr(module, name, server_callable(module_name, name))
    for name in values:
        setattr(module, name, ServerAttributeProxy(module_name, name))


def register_default_route_compatibility(
    module_name: str,
    *,
    broker_routes: Any,
    fragments_routes: Any,
    research_routes: Any,
    webhooks_routes: Any,
    system_routes: Any,
) -> None:
    bind_route_compatibility(
        module_name,
        broker_routes,
        callables=(
            "_get_or_create_broker",
            "build_broker_health_payload",
            "get_order_intent_items",
            "get_order_intent_detail",
            "get_unified_ledger_snapshot",
            "get_ledger_reconcile_report",
            "get_option_contract_summary",
            "get_option_contracts",
        ),
    )
    bind_route_compatibility(
        module_name,
        fragments_routes,
        callables=(
            "_get_cached_value",
            "EventStore",
            "enrich_signal_shadow_payload",
            "get_jobs",
            "get_job",
            "get_bot_events",
            "get_control_actions",
            "get_signal_shadow_report",
            "get_execution_quality_payload",
            "build_promotion_gate_report",
            "get_calibration_runs",
            "get_calibration_points",
            "get_trade_idea",
            "get_trade_ideas",
            "get_trade_ideas_by_analysis",
            "get_idea_transitions",
        ),
        values=("DB_PATH",),
    )
    bind_route_compatibility(
        module_name,
        research_routes,
        callables=(
            "_get_cached_value",
            "_invalidate_cached_values",
            "_invalidate_research_cached_values",
            "_update_research_pipeline_state",
            "create_job",
            "update_job",
            "get_jobs",
            "get_job",
            "create_calibration_run",
            "complete_calibration_run",
            "insert_calibration_points",
            "get_calibration_runs",
            "get_calibration_points",
            "get_calibration_run",
            "get_option_contracts",
            "get_option_contract_summary",
            "get_trade_idea",
            "get_trade_ideas",
            "get_idea_transitions",
            "create_strategy_parameter_set",
            "get_strategy_parameter_sets",
            "get_strategy_parameter_set",
            "get_strategy_promotions",
            "get_fund_daily_reports",
            "build_risk_briefing_payload",
            "build_promotion_gate_report",
            "enrich_signal_shadow_payload",
            "get_signal_shadow_report",
            "run_signal_shadow_cycle",
            "run_tier1_shadow_jobs",
            "get_execution_quality_payload",
            "get_active_strategy_parameter_set",
            "create_order_intent_envelope",
            "promote_strategy_parameter_set",
            "_build_manual_engine_a_trade_instruments",
            "_build_manual_engine_a_trade_sheet",
            "ArtifactStore",
            "EventStore",
            "ModelRouter",
            "DecayReviewService",
            "PilotSignoffService",
            "SynthesisService",
            "PostMortemService",
        ),
        values=("DB_PATH",),
    )
    bind_route_compatibility(
        module_name,
        webhooks_routes,
        callables=(
            "_get_cached_value",
            "_invalidate_research_cached_values",
            "_telegram_reply",
            "_telegram_reply_long",
            "_safe_log_event",
            "_queue_engine_b_intake",
            "_build_tradingview_risk_context",
            "EventStore",
            "FeatureStore",
            "create_job",
            "update_job",
            "analyze_intel_async",
            "get_active_strategy_parameter_set",
            "create_order_intent_envelope",
            "store_factor_grades",
            "evaluate_promotion_gate",
            "get_conn",
        ),
        values=("DB_PATH",),
    )
    bind_route_compatibility(
        module_name,
        system_routes,
        callables=(
            "build_api_health_payload",
            "build_prometheus_metrics_payload",
            "build_status_payload",
            "get_bot_events",
            "get_jobs",
            "get_job",
            "_expire_stale_intel_analysis_jobs",
            "_visible_incidents",
            "get_control_actions",
            "_tail_file",
        ),
    )
