"""Deterministic promotion gate reporting and enforcement for shadow/staged/live lanes.

H-001: Adds enforcement functions that block live execution unless a strategy
has completed the full promotion pipeline (shadow → staged → live) with a
configurable soak period and stale-set detection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from data.trade_db import (
    DB_PATH,
    get_active_strategy_parameter_set,
    get_strategy_promotions,
)
import config
from research.artifacts import ArtifactType, Engine, ProgressionStage, PromotionOutcome
from utils.datetime_utils import parse_iso_utc


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
    return parse_iso_utc(raw)


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
    strategy_key: str = config.DEFAULT_STRATEGY_KEY,
    cooldown_hours: int = 24,
    now_utc: Optional[datetime] = None,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """Build a deterministic lane report with recommended next promotion action."""
    clean_strategy = strategy_key.strip().lower() or config.DEFAULT_STRATEGY_KEY
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


# ─── Enforcement gate (H-001) ───────────────────────────────────────────


@dataclass
class PromotionGateConfig:
    """Configuration for promotion enforcement gate."""

    enabled: bool = True
    min_soak_hours: int = 24
    max_stale_hours: int = 168  # 7 days — live set older than this blocks new entries
    require_live_set: bool = True
    bypass_for_exits: bool = True


@dataclass
class PromotionGateDecision:
    """Result of a promotion enforcement check."""

    allowed: bool
    reason_code: str
    message: str
    strategy_key: str
    live_set_id: Optional[str] = None
    live_version: Optional[int] = None
    soak_remaining_hours: Optional[float] = None
    outcome: PromotionOutcome = PromotionOutcome.PROMOTE
    artifact_refs: list[str] = field(default_factory=list)
    blocking_objections: list[str] = field(default_factory=list)
    research_stage: Optional[str] = None
    requires_human_signoff: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.outcome, str):
            self.outcome = PromotionOutcome(self.outcome)
        if self.outcome == PromotionOutcome.PROMOTE and not self.allowed:
            self.outcome = PromotionOutcome.REJECT
        self.allowed = self.outcome == PromotionOutcome.PROMOTE


def evaluate_promotion_gate(
    strategy_key: str,
    is_exit: bool = False,
    config: Optional[PromotionGateConfig] = None,
    now_utc: Optional[datetime] = None,
    db_path: str = DB_PATH,
) -> PromotionGateDecision:
    """Check whether a strategy is allowed to execute live trades.

    Enforcement rules (entry-only; exits always pass if bypass_for_exits):
    1. Strategy must have an active live-lane parameter set.
    2. The live set must have completed its soak period (min_soak_hours since
       last promotion to live).
    3. The live set must not be stale (promoted more than max_stale_hours ago
       without a refresh).

    Returns a PromotionGateDecision with allowed=True/False and reason codes.
    """
    cfg = config or PromotionGateConfig()
    clean_strategy = (strategy_key or "").strip().lower()
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    # Exits always pass when bypass is enabled
    if is_exit and cfg.bypass_for_exits:
        return PromotionGateDecision(
            allowed=True,
            reason_code="EXIT_BYPASS",
            message="Exits bypass promotion gate.",
            strategy_key=clean_strategy,
            outcome=PromotionOutcome.PROMOTE,
        )

    # Gate disabled — pass through
    if not cfg.enabled:
        return PromotionGateDecision(
            allowed=True,
            reason_code="GATE_DISABLED",
            message="Promotion gate is disabled.",
            strategy_key=clean_strategy,
            outcome=PromotionOutcome.PROMOTE,
        )

    # Check for active live set
    live = get_active_strategy_parameter_set(
        clean_strategy, status="live", db_path=db_path,
    )

    if not live and cfg.require_live_set:
        return PromotionGateDecision(
            allowed=False,
            reason_code="NO_LIVE_SET",
            message=f"Strategy '{clean_strategy}' has no active live-lane parameter set. "
                    "Complete the promotion pipeline (shadow → staged → live) first.",
            strategy_key=clean_strategy,
            outcome=PromotionOutcome.REJECT,
        )

    if not live:
        # require_live_set is False — allow through
        return PromotionGateDecision(
            allowed=True,
            reason_code="LIVE_NOT_REQUIRED",
            message="Live set not required by config.",
            strategy_key=clean_strategy,
            outcome=PromotionOutcome.PROMOTE,
        )

    live_set_id = live.get("id")
    live_version = int(live.get("version") or 0)

    # Find the most recent promotion to live for this strategy
    promotions = get_strategy_promotions(
        limit=20, strategy_key=clean_strategy, db_path=db_path,
    )
    latest_live_promotion_at: Optional[datetime] = None
    for promo in promotions:
        if (promo.get("to_status") or "").strip().lower() == "live":
            latest_live_promotion_at = _parse_iso(str(promo.get("timestamp") or ""))
            break

    # Compute hours since last promotion once for both checks
    hours_since_promotion: Optional[float] = None
    if latest_live_promotion_at:
        hours_since_promotion = (now - latest_live_promotion_at).total_seconds() / 3600.0

    # Soak period check
    if hours_since_promotion is not None and cfg.min_soak_hours > 0:
        if hours_since_promotion < cfg.min_soak_hours:
            remaining = cfg.min_soak_hours - hours_since_promotion
            return PromotionGateDecision(
                allowed=False,
                reason_code="SOAK_PERIOD_ACTIVE",
                message=f"Strategy '{clean_strategy}' live set is in soak period. "
                        f"{remaining:.1f} hours remaining of {cfg.min_soak_hours}h requirement.",
                strategy_key=clean_strategy,
                live_set_id=live_set_id,
                live_version=live_version,
                soak_remaining_hours=round(remaining, 2),
                outcome=PromotionOutcome.PARK,
            )

    # Stale set check
    if hours_since_promotion is not None and cfg.max_stale_hours > 0:
        if hours_since_promotion > cfg.max_stale_hours:
            return PromotionGateDecision(
                allowed=False,
                reason_code="STALE_LIVE_SET",
                message=f"Strategy '{clean_strategy}' live set is stale "
                        f"({hours_since_promotion:.0f}h since last promotion, "
                        f"max {cfg.max_stale_hours}h). Re-promote from staged.",
                strategy_key=clean_strategy,
                live_set_id=live_set_id,
                live_version=live_version,
                outcome=PromotionOutcome.REVISE,
            )

    # All checks passed
    return PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message=f"Strategy '{clean_strategy}' has valid live set (v{live_version}).",
        strategy_key=clean_strategy,
        live_set_id=live_set_id,
        live_version=live_version,
        outcome=PromotionOutcome.PROMOTE,
    )


def evaluate_with_artifacts(
    strategy_key: str,
    artifact_store: Any,
    chain_id: Optional[str] = None,
    is_exit: bool = False,
    config: Optional[PromotionGateConfig] = None,
    now_utc: Optional[datetime] = None,
    db_path: str = DB_PATH,
) -> PromotionGateDecision:
    """Combine live-lane gate checks with artifact-driven promotion outcomes."""
    base_decision = evaluate_promotion_gate(
        strategy_key=strategy_key,
        is_exit=is_exit,
        config=config,
        now_utc=now_utc,
        db_path=db_path,
    )
    if not base_decision.allowed:
        return base_decision

    active_reviews = artifact_store.query(
        artifact_type=ArtifactType.REVIEW_TRIGGER,
        engine=Engine.ENGINE_B,
        ticker=strategy_key,
        limit=20,
    )
    pending_reviews = [artifact for artifact in active_reviews if not artifact.body.get("operator_ack", False)]
    if pending_reviews:
        return PromotionGateDecision(
            allowed=False,
            reason_code="DECAY_REVIEW_PENDING",
            message=f"Decay review pending operator acknowledgement: {pending_reviews[0].body.get('flags', [])}",
            strategy_key=strategy_key,
            live_set_id=base_decision.live_set_id,
            live_version=base_decision.live_version,
            soak_remaining_hours=base_decision.soak_remaining_hours,
            outcome=PromotionOutcome.PARK,
            artifact_refs=[artifact.artifact_id for artifact in pending_reviews if artifact.artifact_id],
        )

    if chain_id is None:
        return base_decision

    chain = artifact_store.get_chain(chain_id)
    if not chain:
        return base_decision

    artifact_refs = [artifact.artifact_id for artifact in chain if artifact.artifact_id]
    latest_scoring = next(
        (artifact for artifact in reversed(chain) if str(artifact.artifact_type) == "ArtifactType.SCORING_RESULT" or getattr(artifact.artifact_type, "value", None) == "scoring_result"),
        None,
    )
    latest_falsification = next(
        (artifact for artifact in reversed(chain) if str(artifact.artifact_type) == "ArtifactType.FALSIFICATION_MEMO" or getattr(artifact.artifact_type, "value", None) == "falsification_memo"),
        None,
    )
    latest_trade_sheet = next(
        (artifact for artifact in reversed(chain) if str(artifact.artifact_type) == "ArtifactType.TRADE_SHEET" or getattr(artifact.artifact_type, "value", None) == "trade_sheet"),
        None,
    )
    latest_pilot_decision = next(
        (artifact for artifact in reversed(chain) if str(artifact.artifact_type) == "ArtifactType.PILOT_DECISION" or getattr(artifact.artifact_type, "value", None) == "pilot_decision"),
        None,
    )

    blocking_objections: list[str] = []
    if latest_scoring:
        blocking_objections = list(latest_scoring.body.get("blocking_objections", []))
        outcome = PromotionOutcome(latest_scoring.body.get("outcome", PromotionOutcome.PROMOTE.value))
        next_stage_text = str(latest_scoring.body.get("next_stage") or "").strip().lower()
        if outcome == PromotionOutcome.PROMOTE and next_stage_text:
            try:
                next_stage = ProgressionStage(next_stage_text)
            except ValueError:
                next_stage = None
            if next_stage == ProgressionStage.TEST:
                return PromotionGateDecision(
                    allowed=False,
                    reason_code="ARTIFACT_STAGE_TEST_PENDING",
                    message="Artifact chain is test-ready but has not advanced beyond the research sandbox.",
                    strategy_key=strategy_key,
                    live_set_id=base_decision.live_set_id,
                    live_version=base_decision.live_version,
                    soak_remaining_hours=base_decision.soak_remaining_hours,
                    outcome=PromotionOutcome.REVISE,
                    artifact_refs=artifact_refs,
                    blocking_objections=blocking_objections,
                    research_stage=next_stage.value,
                )
            if next_stage == ProgressionStage.EXPERIMENT:
                return PromotionGateDecision(
                    allowed=False,
                    reason_code="ARTIFACT_STAGE_EXPERIMENT_PENDING",
                    message="Artifact chain is experiment-ready but not yet pilot-ready for live capital.",
                    strategy_key=strategy_key,
                    live_set_id=base_decision.live_set_id,
                    live_version=base_decision.live_version,
                    soak_remaining_hours=base_decision.soak_remaining_hours,
                    outcome=PromotionOutcome.REVISE,
                    artifact_refs=artifact_refs,
                    blocking_objections=blocking_objections,
                    research_stage=next_stage.value,
                )
            if next_stage == ProgressionStage.PILOT and latest_trade_sheet is None:
                return PromotionGateDecision(
                    allowed=False,
                    reason_code="ARTIFACT_PILOT_CHAIN_INCOMPLETE",
                    message="Artifact chain targets pilot but does not yet include a TradeSheet.",
                    strategy_key=strategy_key,
                    live_set_id=base_decision.live_set_id,
                    live_version=base_decision.live_version,
                    soak_remaining_hours=base_decision.soak_remaining_hours,
                    outcome=PromotionOutcome.REVISE,
                    artifact_refs=artifact_refs,
                    blocking_objections=blocking_objections,
                    research_stage=next_stage.value,
                )
            if next_stage == ProgressionStage.PILOT:
                if latest_pilot_decision is None:
                    return PromotionGateDecision(
                        allowed=False,
                        reason_code="ARTIFACT_PILOT_SIGNOFF_PENDING",
                        message="Artifact chain is pilot-ready and awaiting human sign-off.",
                        strategy_key=strategy_key,
                        live_set_id=base_decision.live_set_id,
                        live_version=base_decision.live_version,
                        soak_remaining_hours=base_decision.soak_remaining_hours,
                        outcome=PromotionOutcome.PARK,
                        artifact_refs=artifact_refs,
                        blocking_objections=blocking_objections,
                        research_stage=next_stage.value,
                        requires_human_signoff=True,
                    )
                if not bool(latest_pilot_decision.body.get("approved")):
                    note = str(latest_pilot_decision.body.get("operator_notes") or "").strip()
                    message = "Pilot sign-off was rejected by the operator."
                    if note:
                        message = f"{message} {note}"
                    return PromotionGateDecision(
                        allowed=False,
                        reason_code="ARTIFACT_PILOT_REJECTED",
                        message=message,
                        strategy_key=strategy_key,
                        live_set_id=base_decision.live_set_id,
                        live_version=base_decision.live_version,
                        soak_remaining_hours=base_decision.soak_remaining_hours,
                        outcome=PromotionOutcome.REJECT,
                        artifact_refs=artifact_refs,
                        blocking_objections=blocking_objections,
                        research_stage=next_stage.value,
                    )
                return PromotionGateDecision(
                    allowed=True,
                    reason_code="ARTIFACT_PILOT_APPROVED",
                    message="Artifact chain is pilot-ready and operator-approved.",
                    strategy_key=strategy_key,
                    live_set_id=base_decision.live_set_id,
                    live_version=base_decision.live_version,
                    soak_remaining_hours=base_decision.soak_remaining_hours,
                    outcome=PromotionOutcome.PROMOTE,
                    artifact_refs=artifact_refs,
                    blocking_objections=blocking_objections,
                    research_stage=next_stage.value,
                    requires_human_signoff=False,
                )
        return PromotionGateDecision(
            allowed=outcome == PromotionOutcome.PROMOTE,
            reason_code=f"ARTIFACT_{outcome.value.upper()}",
            message=f"Artifact chain evaluated to {outcome.value}.",
            strategy_key=strategy_key,
            live_set_id=base_decision.live_set_id,
            live_version=base_decision.live_version,
            soak_remaining_hours=base_decision.soak_remaining_hours,
            outcome=outcome,
            artifact_refs=artifact_refs,
            blocking_objections=blocking_objections,
            research_stage=next_stage_text or None,
            requires_human_signoff=False,
        )

    if latest_falsification:
        blocking_objections = list(latest_falsification.body.get("unresolved_objections", []))
        if blocking_objections:
            return PromotionGateDecision(
                allowed=False,
                reason_code="ARTIFACT_UNRESOLVED_OBJECTIONS",
                message="Artifact chain has unresolved objections.",
                strategy_key=strategy_key,
                live_set_id=base_decision.live_set_id,
                live_version=base_decision.live_version,
                soak_remaining_hours=base_decision.soak_remaining_hours,
                outcome=PromotionOutcome.REVISE,
                artifact_refs=artifact_refs,
                blocking_objections=blocking_objections,
            )

    base_decision.artifact_refs = artifact_refs
    return base_decision
