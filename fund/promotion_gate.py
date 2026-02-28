"""Deterministic promotion gate reporting for shadow/staged/live lanes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from data.trade_db import (
    DB_PATH,
    get_active_strategy_parameter_set,
    get_strategy_promotions,
)


REASON_TEXT = {
    "NO_LANE_DATA": "No parameter sets found in any lane.",
    "SHADOW_SET_AVAILABLE": "Shadow lane has a candidate set.",
    "STAGED_LIVE_MISSING": "Staged-live lane is empty.",
    "STAGED_SET_AVAILABLE": "Staged-live lane has a candidate set.",
    "LIVE_MISSING": "Live lane is empty.",
    "STAGED_NEWER_THAN_LIVE": "Staged-live version is newer than live.",
    "LIVE_UP_TO_DATE": "Live lane is up to date.",
    "LIVE_ONLY_NO_CANDIDATE": "Live lane exists but no shadow/staged candidate is available.",
    "PROMOTION_COOLDOWN_ACTIVE": "Promotion cooldown window is active.",
}


def _parse_iso(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_ts(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _lane_payload(item: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not item:
        return {
            "status": "missing",
            "set_id": None,
            "version": None,
            "name": None,
            "updated_at": None,
        }
    return {
        "status": "active",
        "set_id": item.get("id"),
        "version": item.get("version"),
        "name": item.get("name"),
        "updated_at": item.get("updated_at"),
    }


def _reason_texts(reason_codes: list[str]) -> list[str]:
    return [REASON_TEXT.get(code, code) for code in reason_codes]


def validate_lane_transition(from_status: str, to_status: str) -> tuple[bool, list[str]]:
    """Validate one promotion transition against the 3-lane policy."""
    source = (from_status or "").strip().lower()
    target = (to_status or "").strip().lower()

    if source not in {"shadow", "staged_live", "live", "archived"}:
        return False, ["UNKNOWN_SOURCE_STATUS"]
    if target not in {"shadow", "staged_live", "live", "archived"}:
        return False, ["INVALID_TARGET_STATUS"]
    if source == target:
        return False, ["NO_OP_TRANSITION"]

    allowed_targets = {
        "shadow": {"staged_live", "archived"},
        "staged_live": {"live", "archived"},
        "live": {"archived"},
        "archived": {"shadow"},
    }
    if target in allowed_targets[source]:
        return True, []
    return False, ["INVALID_LANE_TRANSITION"]


def build_promotion_gate_report(
    strategy_key: str = "ibs_credit_spreads",
    cooldown_hours: int = 24,
    now_utc: Optional[datetime] = None,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """Build a deterministic lane report with recommended next promotion action."""
    clean_strategy = strategy_key.strip().lower() or "ibs_credit_spreads"
    cooldown = max(0, int(cooldown_hours))
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    shadow = get_active_strategy_parameter_set(clean_strategy, status="shadow", db_path=db_path)
    staged = get_active_strategy_parameter_set(clean_strategy, status="staged_live", db_path=db_path)
    live = get_active_strategy_parameter_set(clean_strategy, status="live", db_path=db_path)
    promotions = get_strategy_promotions(limit=20, strategy_key=clean_strategy, db_path=db_path)

    latest_promotion_at: Optional[datetime] = None
    if promotions:
        latest_promotion_at = _parse_iso(str(promotions[0].get("timestamp") or ""))

    cooldown_active = False
    if latest_promotion_at and cooldown > 0:
        cooldown_active = (now - latest_promotion_at) < timedelta(hours=cooldown)

    action = "HOLD"
    target_set_id: Optional[str] = None
    reason_codes: list[str] = []

    if not shadow and not staged and not live:
        reason_codes = ["NO_LANE_DATA"]
    elif shadow and not staged:
        action = "PROMOTE_SHADOW_TO_STAGED"
        target_set_id = shadow.get("id")
        reason_codes = ["SHADOW_SET_AVAILABLE", "STAGED_LIVE_MISSING"]
    elif staged and not live:
        action = "PROMOTE_STAGED_TO_LIVE"
        target_set_id = staged.get("id")
        reason_codes = ["STAGED_SET_AVAILABLE", "LIVE_MISSING"]
    elif staged and live:
        staged_version = int(staged.get("version") or 0)
        live_version = int(live.get("version") or 0)
        if staged_version > live_version:
            action = "PROMOTE_STAGED_TO_LIVE"
            target_set_id = staged.get("id")
            reason_codes = ["STAGED_NEWER_THAN_LIVE"]
        else:
            reason_codes = ["LIVE_UP_TO_DATE"]
    else:
        reason_codes = ["LIVE_ONLY_NO_CANDIDATE"]

    if action != "HOLD" and cooldown_active:
        action = "HOLD"
        reason_codes.append("PROMOTION_COOLDOWN_ACTIVE")

    staged_version = int(staged.get("version") or 0) if staged else None
    live_version = int(live.get("version") or 0) if live else None
    version_gap = None
    if staged_version is not None and live_version is not None:
        version_gap = staged_version - live_version

    return {
        "strategy_key": clean_strategy,
        "generated_at": _format_ts(now),
        "cooldown_hours": cooldown,
        "cooldown_active": cooldown_active,
        "latest_promotion_at": _format_ts(latest_promotion_at),
        "lanes": {
            "shadow": _lane_payload(shadow),
            "staged_live": _lane_payload(staged),
            "live": _lane_payload(live),
        },
        "comparison": {
            "staged_vs_live_version_gap": version_gap,
            "shadow_version": int(shadow.get("version") or 0) if shadow else None,
            "staged_live_version": staged_version,
            "live_version": live_version,
        },
        "recommendation": {
            "action": action,
            "target_set_id": target_set_id,
            "reason_codes": reason_codes,
            "reason_text": _reason_texts(reason_codes),
        },
        "recent_promotions": [
            {
                "timestamp": row.get("timestamp"),
                "set_id": row.get("set_id"),
                "from_status": row.get("from_status"),
                "to_status": row.get("to_status"),
                "actor": row.get("actor"),
            }
            for row in promotions[:10]
        ],
    }
