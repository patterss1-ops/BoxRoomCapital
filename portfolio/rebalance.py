"""Deterministic sleeve-drift rebalancing planner.

H-002 (slice 1): compute rebalance actions from current sleeve NAVs versus
target sleeve weights. This module is intentionally side-effect free so the
scheduler can call it safely before wiring broker execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

import config
from data.trade_db import DB_PATH, get_sleeve_daily_reports


@dataclass(frozen=True)
class RebalanceAction:
    """One sleeve-level rebalance recommendation."""

    sleeve: str
    current_nav: float
    target_nav: float
    delta_nav: float
    current_weight_pct: float
    target_weight_pct: float
    drift_pct: float
    action: str
    exceeds_threshold: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RebalancePlan:
    """Full rebalance plan for a report date."""

    report_date: str
    generated_at: str
    total_nav: float
    drift_threshold_pct: float
    min_trade_notional: float
    target_weight_by_sleeve: dict[str, float]
    actions: list[RebalanceAction]

    @property
    def requires_rebalance(self) -> bool:
        return any(a.exceeds_threshold for a in self.actions)

    def to_dict(self) -> dict[str, Any]:
        intents = [intent.to_dict() for intent in build_rebalance_intents(self)]
        return {
            "report_date": self.report_date,
            "generated_at": self.generated_at,
            "total_nav": self.total_nav,
            "drift_threshold_pct": self.drift_threshold_pct,
            "min_trade_notional": self.min_trade_notional,
            "target_weight_by_sleeve": dict(self.target_weight_by_sleeve),
            "requires_rebalance": self.requires_rebalance,
            "actions": [a.to_dict() for a in self.actions],
            "rebalance_intents": intents,
            "rebalance_intents_count": len(intents),
        }


@dataclass(frozen=True)
class RebalanceIntent:
    """Executable intent produced from a threshold-breaching drift action."""

    sleeve: str
    side: str
    notional: float
    reason: str
    drift_pct: float
    current_weight_pct: float
    target_weight_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_target_weights_from_slots(
    slot_configs: Optional[list[dict[str, Any]]] = None,
) -> dict[str, float]:
    """Build equal target weights across enabled sleeves in STRATEGY_SLOTS."""
    configs = slot_configs
    if configs is None:
        configs = getattr(config, "STRATEGY_SLOTS", [])

    sleeves = sorted(
        {
            str(raw.get("sleeve", "")).strip()
            for raw in configs
            if raw.get("enabled", True) and str(raw.get("sleeve", "")).strip()
        }
    )
    if not sleeves:
        return {}

    w = 1.0 / float(len(sleeves))
    return {sleeve: w for sleeve in sleeves}


def load_latest_sleeve_navs(
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict[str, float]:
    """Load sleeve NAVs for one report date (latest by default)."""
    _, nav_by_sleeve = _load_sleeve_nav_snapshot(
        report_date=report_date,
        db_path=db_path,
    )
    return nav_by_sleeve


def build_rebalance_plan(
    current_nav_by_sleeve: Mapping[str, float],
    target_weight_by_sleeve: Mapping[str, float],
    drift_threshold_pct: float = 5.0,
    min_trade_notional: float = 0.0,
    report_date: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> RebalancePlan:
    """Compute sleeve drift and a deterministic rebalance action list."""
    if drift_threshold_pct < 0:
        raise ValueError("drift_threshold_pct must be >= 0")
    if min_trade_notional < 0:
        raise ValueError("min_trade_notional must be >= 0")

    target = _normalize_weights(target_weight_by_sleeve)
    current = {
        str(sleeve).strip(): float(nav or 0.0)
        for sleeve, nav in current_nav_by_sleeve.items()
        if str(sleeve).strip()
    }

    total_nav = float(sum(current.values()))
    sleeves = sorted(set(current.keys()) | set(target.keys()))
    report = report_date or date.today().isoformat()
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    actions: list[RebalanceAction] = []

    for sleeve in sleeves:
        current_nav = float(current.get(sleeve, 0.0))
        target_weight = float(target.get(sleeve, 0.0))

        current_weight_pct = (
            (current_nav / total_nav) * 100.0 if total_nav > 0.0 else 0.0
        )
        target_weight_pct = target_weight * 100.0
        drift_pct = current_weight_pct - target_weight_pct

        target_nav = target_weight * total_nav if total_nav > 0.0 else 0.0
        delta_nav = target_nav - current_nav

        if delta_nav > 0:
            action = "BUY"
        elif delta_nav < 0:
            action = "SELL"
        else:
            action = "HOLD"

        exceeds_threshold = (
            total_nav > 0.0
            and abs(drift_pct) >= drift_threshold_pct
            and abs(delta_nav) >= min_trade_notional
        )

        actions.append(
            RebalanceAction(
                sleeve=sleeve,
                current_nav=current_nav,
                target_nav=target_nav,
                delta_nav=delta_nav,
                current_weight_pct=current_weight_pct,
                target_weight_pct=target_weight_pct,
                drift_pct=drift_pct,
                action=action,
                exceeds_threshold=exceeds_threshold,
            )
        )

    actions.sort(key=lambda a: (-abs(a.drift_pct), a.sleeve))
    return RebalancePlan(
        report_date=report,
        generated_at=generated,
        total_nav=total_nav,
        drift_threshold_pct=float(drift_threshold_pct),
        min_trade_notional=float(min_trade_notional),
        target_weight_by_sleeve=dict(target),
        actions=actions,
    )


def run_rebalance_drift_check(
    target_weight_by_sleeve: Optional[Mapping[str, float]] = None,
    drift_threshold_pct: float = 5.0,
    min_trade_notional: float = 0.0,
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> RebalancePlan:
    """Load latest sleeve NAVs and generate a rebalance plan."""
    resolved_date, current_nav_by_sleeve = _load_sleeve_nav_snapshot(
        report_date=report_date,
        db_path=db_path,
    )
    target = (
        dict(target_weight_by_sleeve)
        if target_weight_by_sleeve is not None
        else default_target_weights_from_slots()
    )
    return build_rebalance_plan(
        current_nav_by_sleeve=current_nav_by_sleeve,
        target_weight_by_sleeve=target,
        drift_threshold_pct=drift_threshold_pct,
        min_trade_notional=min_trade_notional,
        report_date=resolved_date or report_date or date.today().isoformat(),
    )


def build_rebalance_intents(plan: RebalancePlan) -> list[RebalanceIntent]:
    """Convert drift actions into sleeve-level rebalance intents."""
    intents: list[RebalanceIntent] = []
    for action in plan.actions:
        if not action.exceeds_threshold:
            continue
        if action.action not in {"BUY", "SELL"}:
            continue
        notional = abs(float(action.delta_nav))
        if notional <= 0:
            continue
        intents.append(
            RebalanceIntent(
                sleeve=action.sleeve,
                side=action.action,
                notional=notional,
                reason=(
                    f"drift={action.drift_pct:.2f}% "
                    f"target={action.target_weight_pct:.2f}% "
                    f"current={action.current_weight_pct:.2f}%"
                ),
                drift_pct=action.drift_pct,
                current_weight_pct=action.current_weight_pct,
                target_weight_pct=action.target_weight_pct,
            )
        )
    return intents


class DriftPlanner:
    """Stateful wrapper used by scheduler/pipeline integrations."""

    def __init__(
        self,
        target_weight_by_sleeve: Optional[Mapping[str, float]] = None,
        drift_threshold_pct: float = 5.0,
        min_trade_notional: float = 0.0,
        db_path: str = DB_PATH,
    ):
        self.target_weight_by_sleeve = (
            dict(target_weight_by_sleeve) if target_weight_by_sleeve is not None else None
        )
        self.drift_threshold_pct = float(drift_threshold_pct)
        self.min_trade_notional = float(min_trade_notional)
        self.db_path = db_path

    def check(self, report_date: Optional[str] = None) -> RebalancePlan:
        return run_rebalance_drift_check(
            target_weight_by_sleeve=self.target_weight_by_sleeve,
            drift_threshold_pct=self.drift_threshold_pct,
            min_trade_notional=self.min_trade_notional,
            report_date=report_date,
            db_path=self.db_path,
        )

    def intents(self, report_date: Optional[str] = None) -> list[RebalanceIntent]:
        return build_rebalance_intents(self.check(report_date=report_date))


def _normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Normalize arbitrary positive weights into a sum-to-1 allocation."""
    cleaned: dict[str, float] = {}
    total = 0.0
    for sleeve, raw in weights.items():
        key = str(sleeve).strip()
        if not key:
            continue
        value = float(raw)
        if value < 0:
            raise ValueError("target weights must be >= 0")
        if value == 0:
            continue
        cleaned[key] = value
        total += value

    if cleaned and total <= 0:
        raise ValueError("target weights must sum to > 0")
    if not cleaned:
        return {}
    return {k: v / total for k, v in cleaned.items()}


def _load_sleeve_nav_snapshot(
    report_date: Optional[str],
    db_path: str,
) -> tuple[Optional[str], dict[str, float]]:
    rows = get_sleeve_daily_reports(days=120, db_path=db_path)
    if not rows:
        return None, {}

    resolved = report_date or str(rows[0].get("report_date") or "")
    if not resolved:
        return None, {}

    nav_by_sleeve: dict[str, float] = {}
    for row in rows:
        if str(row.get("report_date")) != resolved:
            continue
        sleeve = str(row.get("sleeve", "")).strip()
        if not sleeve:
            continue
        nav_by_sleeve[sleeve] = nav_by_sleeve.get(sleeve, 0.0) + float(
            row.get("nav") or 0.0
        )
    return resolved, nav_by_sleeve
