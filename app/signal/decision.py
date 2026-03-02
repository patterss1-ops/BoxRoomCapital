"""Decision helpers for Signal Engine composite outputs (E-006)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from app.signal.contracts import LayerScore, decide_action
from app.signal.types import DecisionAction, LayerId, ScoreThresholds


def normalize_vetoes(vetoes: Iterable[str]) -> Tuple[str, ...]:
    """Normalize veto codes into a deduplicated, order-preserving tuple."""
    seen = set()
    normalized: List[str] = []
    for raw in vetoes:
        code = str(raw or "").strip().lower()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return tuple(normalized)


def extract_layer_vetoes(layer_scores: Sequence[LayerScore]) -> Tuple[str, ...]:
    """Extract veto codes from per-layer score payload details."""
    vetoes: List[str] = []

    for item in layer_scores:
        details = item.details or {}

        # Preferred shape: explicit veto list from layer.
        raw_list = details.get("vetoes")
        if isinstance(raw_list, (list, tuple, set)):
            for code in raw_list:
                vetoes.append(str(code))

        # Fallback shape used by L2 insider adapter.
        if details.get("vetoed") is True and item.layer_id == LayerId.L2_INSIDER:
            vetoes.append("insider_sell_cluster")

        # Generic explicit single-code fallback.
        reason = details.get("veto_reason")
        if reason:
            vetoes.append(str(reason))

    return normalize_vetoes(vetoes)


@dataclass(frozen=True)
class VetoPolicy:
    """Policy mapping veto codes to action overrides."""

    hard_block_vetoes: Tuple[str, ...] = (
        "insider_sell_cluster",
        "risk_hard_stop",
        "kill_switch_active",
        "account_router_reject",
    )
    force_short_vetoes: Tuple[str, ...] = ()


def resolve_action(
    final_score: float,
    thresholds: ScoreThresholds,
    vetoes: Iterable[str] = (),
    policy: VetoPolicy = VetoPolicy(),
) -> DecisionAction:
    """Resolve final action from score thresholds + veto policy."""
    normalized = normalize_vetoes(vetoes)
    base = decide_action(score=final_score, thresholds=thresholds)

    if not normalized:
        return base

    veto_set = set(normalized)
    if veto_set.intersection(policy.force_short_vetoes):
        return DecisionAction.SHORT_CANDIDATE

    if veto_set.intersection(policy.hard_block_vetoes):
        return DecisionAction.NO_ACTION

    return base
