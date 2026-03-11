"""Shared editable-settings helpers."""
from __future__ import annotations

from typing import Any, Callable


def _get_editable_settings(config_module: Any) -> dict[str, Any]:
    overrides = config_module._load_runtime_overrides()
    return {
        "broker": {
            "broker_mode": overrides.get("broker_mode", config_module.BROKER_MODE),
            "trading_mode": overrides.get("trading_mode", config_module.TRADING_MODE),
        },
        "risk_limits": {
            "portfolio_initial_capital": overrides.get("portfolio_initial_capital", config_module.PORTFOLIO["initial_capital"]),
            "portfolio_default_stake": overrides.get("portfolio_default_stake", config_module.PORTFOLIO["default_stake_per_point"]),
            "portfolio_max_positions": overrides.get("portfolio_max_positions", config_module.PORTFOLIO["max_open_positions"]),
            "portfolio_max_exposure_pct": overrides.get("portfolio_max_exposure_pct", config_module.PORTFOLIO["max_exposure_pct"]),
        },
        "ibs_parameters": {
            "ibs_entry_thresh": overrides.get("ibs_entry_thresh", config_module.IBS_PARAMS["ibs_entry_thresh"]),
            "ibs_exit_thresh": overrides.get("ibs_exit_thresh", config_module.IBS_PARAMS["ibs_exit_thresh"]),
            "ibs_use_rsi_filter": overrides.get("ibs_use_rsi_filter", config_module.IBS_PARAMS["use_rsi_filter"]),
            "ibs_rsi_period": overrides.get("ibs_rsi_period", config_module.IBS_PARAMS["rsi_period"]),
            "ibs_rsi_entry_thresh": overrides.get("ibs_rsi_entry_thresh", config_module.IBS_PARAMS["rsi_entry_thresh"]),
            "ibs_rsi_exit_thresh": overrides.get("ibs_rsi_exit_thresh", config_module.IBS_PARAMS["rsi_exit_thresh"]),
            "ibs_ema_period": overrides.get("ibs_ema_period", config_module.IBS_PARAMS["ema_period"]),
        },
        "notifications": {
            "notifications_enabled": overrides.get("notifications_enabled", config_module.NOTIFICATIONS["enabled"]),
            "notifications_email_to": overrides.get("notifications_email_to", config_module.NOTIFICATIONS["email_to"]),
            "notifications_telegram_chat_id": overrides.get("notifications_telegram_chat_id", config_module.NOTIFICATIONS["telegram_chat_id"]),
        },
        "council_research": {
            "council_model_timeout": overrides.get("council_model_timeout", config_module.COUNCIL_MODEL_TIMEOUT),
            "council_round_timeout": overrides.get("council_round_timeout", config_module.COUNCIL_ROUND_TIMEOUT),
            "idea_research_auto": overrides.get("idea_research_auto", config_module.IDEA_RESEARCH_AUTO),
            "idea_review_min_score": overrides.get("idea_review_min_score", config_module.IDEA_REVIEW_MIN_SCORE),
            "idea_auto_promote_backtest": overrides.get("idea_auto_promote_backtest", config_module.IDEA_AUTO_PROMOTE_BACKTEST),
            "idea_auto_promote_paper": overrides.get("idea_auto_promote_paper", config_module.IDEA_AUTO_PROMOTE_PAPER),
            "idea_dynamic_bt_min_sharpe": overrides.get("idea_dynamic_bt_min_sharpe", config_module.IDEA_DYNAMIC_BT_MIN_SHARPE),
            "idea_dynamic_bt_min_pf": overrides.get("idea_dynamic_bt_min_pf", config_module.IDEA_DYNAMIC_BT_MIN_PF),
            "idea_dynamic_bt_min_trades": overrides.get("idea_dynamic_bt_min_trades", config_module.IDEA_DYNAMIC_BT_MIN_TRADES),
        },
    }


def _validate_settings(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "broker_mode" in data and data["broker_mode"] not in ("paper", "demo", "live"):
        errors.append("broker_mode must be paper, demo, or live.")
    if "trading_mode" in data and data["trading_mode"] not in ("shadow", "live"):
        errors.append("trading_mode must be shadow or live.")
    float_fields = {
        "portfolio_initial_capital": (100, 10_000_000),
        "portfolio_default_stake": (0.01, 1000),
        "portfolio_max_exposure_pct": (1, 100),
        "ibs_entry_thresh": (0.01, 0.99),
        "ibs_exit_thresh": (0.01, 0.99),
        "ibs_rsi_entry_thresh": (1, 99),
        "ibs_rsi_exit_thresh": (1, 99),
        "idea_review_min_score": (0, 10),
        "idea_dynamic_bt_min_sharpe": (-5, 10),
        "idea_dynamic_bt_min_pf": (0, 10),
    }
    for field, (lo, hi) in float_fields.items():
        if field in data:
            try:
                val = float(data[field])
                if val < lo or val > hi:
                    errors.append(f"{field} must be between {lo} and {hi}.")
            except (ValueError, TypeError):
                errors.append(f"{field} must be a number.")
    int_fields = {
        "portfolio_max_positions": (1, 100),
        "ibs_rsi_period": (1, 50),
        "ibs_ema_period": (10, 500),
        "council_model_timeout": (15, 300),
        "council_round_timeout": (20, 600),
        "idea_dynamic_bt_min_trades": (1, 1000),
    }
    for field, (lo, hi) in int_fields.items():
        if field in data:
            try:
                val = int(data[field])
                if val < lo or val > hi:
                    errors.append(f"{field} must be between {lo} and {hi}.")
            except (ValueError, TypeError):
                errors.append(f"{field} must be an integer.")
    return errors


def _save_settings_overrides(
    data: dict[str, Any],
    *,
    config_module: Any,
    atomic_write_json: Callable[[Any, Any], None],
) -> None:
    existing = config_module._load_runtime_overrides()
    type_casts = {
        "portfolio_initial_capital": float,
        "portfolio_default_stake": float,
        "portfolio_max_positions": int,
        "portfolio_max_exposure_pct": float,
        "ibs_entry_thresh": float,
        "ibs_exit_thresh": float,
        "ibs_use_rsi_filter": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
        "ibs_rsi_period": int,
        "ibs_rsi_entry_thresh": float,
        "ibs_rsi_exit_thresh": float,
        "ibs_ema_period": int,
        "notifications_enabled": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
        "council_model_timeout": int,
        "council_round_timeout": int,
        "idea_research_auto": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
        "idea_review_min_score": float,
        "idea_auto_promote_backtest": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
        "idea_auto_promote_paper": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on"),
        "idea_dynamic_bt_min_sharpe": float,
        "idea_dynamic_bt_min_pf": float,
        "idea_dynamic_bt_min_trades": int,
    }
    for key, value in data.items():
        if key in type_casts:
            existing[key] = type_casts[key](value)
        else:
            existing[key] = value
    config_module._RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(config_module._SETTINGS_OVERRIDE_PATH, existing)

