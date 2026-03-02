"""Composite scorer, convergence bonus, and veto evaluation (E-006)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from app.signal.contracts import CompositeRequest, CompositeResult
from app.signal.decision import (
    VetoPolicy,
    extract_layer_vetoes,
    normalize_vetoes,
    resolve_action,
)
from app.signal.types import LayerId


@dataclass(frozen=True)
class CompositeScoringConfig:
    """Tunable settings for composite score behavior."""

    bullish_threshold: float = 65.0
    bearish_threshold: float = 35.0
    neutral_anchor: float = 50.0
    min_layers_for_bonus: int = 3
    alignment_required: float = 0.75
    max_convergence_bonus_pct: float = 12.0
    # Optional set of required layers. Missing entries emit `missing_required_veto_code`.
    required_layers: Tuple[LayerId, ...] = ()
    missing_required_veto_code: str = "missing_required_layers"
    stale_layer_veto_code: str = "stale_layer_data"
    emit_missing_required_veto: bool = False
    emit_stale_layer_veto: bool = True
    # Data quality penalties are applied after convergence multiplier.
    # Freshness state comes from layer details (`_freshness_state`) when available.
    warning_layer_penalty_pct: float = 1.5
    stale_layer_penalty_pct: float = 12.0
    max_data_quality_penalty_pct: float = 40.0
    # Optional hard floor by layer. If breached, emits veto code
    # `layer_floor_breach:<layer_id>`.
    layer_score_floors: Dict[LayerId, float] = field(default_factory=dict)


def _active_weights(request: CompositeRequest) -> Dict[LayerId, float]:
    """Resolve layer weights and renormalize to layers present in the request."""
    resolved = request.resolved_weights()
    active_ids = tuple(item.layer_id for item in request.layer_scores)

    active_weights: Dict[LayerId, float] = {
        layer_id: float(resolved[layer_id])
        for layer_id in active_ids
    }
    total = sum(active_weights.values())
    if total <= 0:
        raise ValueError("Active layer weight total must be > 0.")

    return {
        layer_id: weight / total
        for layer_id, weight in active_weights.items()
    }


def compute_weighted_score(request: CompositeRequest) -> float:
    """Compute weighted score from request layers and normalized active weights."""
    weights = _active_weights(request)
    score_map = request.score_map()
    weighted = 0.0

    for layer_id, layer_score in score_map.items():
        weighted += float(weights[layer_id]) * float(layer_score)

    return round(weighted, 6)


def _convergence_direction(
    layer_scores: Mapping[LayerId, float],
    cfg: CompositeScoringConfig,
) -> Tuple[str, int, int]:
    """Return dominant direction label and counts.

    Returns tuple: ("bullish"|"bearish"|"mixed", dominant_count, total_count)
    """
    total = len(layer_scores)
    if total == 0:
        return "mixed", 0, 0

    bullish = sum(1 for score in layer_scores.values() if float(score) >= cfg.bullish_threshold)
    bearish = sum(1 for score in layer_scores.values() if float(score) <= cfg.bearish_threshold)

    if bullish > bearish:
        return "bullish", bullish, total
    if bearish > bullish:
        return "bearish", bearish, total
    return "mixed", bullish, total


def compute_convergence_bonus(
    layer_scores: Mapping[LayerId, float],
    config: CompositeScoringConfig = CompositeScoringConfig(),
) -> float:
    """Compute convergence bonus percentage from directional agreement.

    Bonus is awarded only when enough layers strongly agree in one direction.
    """
    direction, dominant_count, total = _convergence_direction(layer_scores, config)

    if total < config.min_layers_for_bonus:
        return 0.0
    if direction == "mixed":
        return 0.0

    alignment = dominant_count / float(total)
    if alignment < config.alignment_required:
        return 0.0

    if direction == "bullish":
        aligned_scores = [
            float(score)
            for score in layer_scores.values()
            if float(score) >= config.bullish_threshold
        ]
        distances = [max(0.0, score - config.neutral_anchor) for score in aligned_scores]
    else:
        aligned_scores = [
            float(score)
            for score in layer_scores.values()
            if float(score) <= config.bearish_threshold
        ]
        distances = [max(0.0, config.neutral_anchor - score) for score in aligned_scores]

    if not distances:
        return 0.0

    avg_distance = sum(distances) / len(distances)
    intensity_factor = min(1.0, avg_distance / 50.0)

    # Once the minimum alignment gate is met, reward stronger agreement linearly.
    alignment_factor = max(0.0, min(1.0, alignment))

    bonus = float(config.max_convergence_bonus_pct) * alignment_factor * intensity_factor
    return round(max(0.0, bonus), 6)


def _evaluate_data_quality(
    request: CompositeRequest,
    config: CompositeScoringConfig,
) -> Tuple[Tuple[LayerId, ...], Tuple[LayerId, ...], Tuple[LayerId, ...]]:
    """Return (missing_required, warning_layers, stale_layers)."""
    required = tuple(config.required_layers or ())
    present = {item.layer_id for item in request.layer_scores}
    missing_required = tuple(layer_id for layer_id in required if layer_id not in present)

    warning_layers: list[LayerId] = []
    stale_layers: list[LayerId] = []

    for item in request.layer_scores:
        details = item.details or {}
        freshness = str(details.get("_freshness_state") or "").strip().lower()
        if freshness == "warning":
            warning_layers.append(item.layer_id)
        elif freshness == "stale":
            stale_layers.append(item.layer_id)

    return missing_required, tuple(warning_layers), tuple(stale_layers)


def _compute_data_quality_penalty_pct(
    warning_layers: Sequence[LayerId],
    stale_layers: Sequence[LayerId],
    config: CompositeScoringConfig,
) -> float:
    penalty = (
        (len(tuple(warning_layers)) * float(config.warning_layer_penalty_pct))
        + (len(tuple(stale_layers)) * float(config.stale_layer_penalty_pct))
    )
    capped = max(0.0, min(float(config.max_data_quality_penalty_pct), penalty))
    return round(capped, 6)


def evaluate_vetoes(
    request: CompositeRequest,
    external_vetoes: Iterable[str] = (),
    config: CompositeScoringConfig = CompositeScoringConfig(),
) -> Tuple[str, ...]:
    """Aggregate veto codes from layers, external systems, and floor rules."""
    vetoes = list(extract_layer_vetoes(request.layer_scores))
    vetoes.extend(list(external_vetoes or ()))

    floors = dict(config.layer_score_floors or {})
    if floors:
        for item in request.layer_scores:
            floor = floors.get(item.layer_id)
            if floor is None:
                continue
            if float(item.score) < float(floor):
                vetoes.append(f"layer_floor_breach:{item.layer_id.value}")

    return normalize_vetoes(vetoes)


def evaluate_composite(
    request: CompositeRequest,
    external_vetoes: Iterable[str] = (),
    scoring_config: CompositeScoringConfig = CompositeScoringConfig(),
    veto_policy: VetoPolicy = VetoPolicy(),
) -> CompositeResult:
    """Compute final composite result with bonus, vetoes, and decision."""
    weighted = compute_weighted_score(request)
    score_map = request.score_map()
    bonus_pct = compute_convergence_bonus(score_map, config=scoring_config)
    missing_required, warning_layers, stale_layers = _evaluate_data_quality(
        request=request,
        config=scoring_config,
    )

    quality_vetoes: list[str] = []
    if missing_required and bool(scoring_config.emit_missing_required_veto):
        code = str(scoring_config.missing_required_veto_code or "").strip().lower()
        if code:
            quality_vetoes.append(code)
    if stale_layers and bool(scoring_config.emit_stale_layer_veto):
        code = str(scoring_config.stale_layer_veto_code or "").strip().lower()
        if code:
            quality_vetoes.append(code)

    merged_external_vetoes = tuple(external_vetoes or ()) + tuple(quality_vetoes)
    vetoes = evaluate_vetoes(
        request,
        external_vetoes=merged_external_vetoes,
        config=scoring_config,
    )
    direction, _, _ = _convergence_direction(score_map, scoring_config)
    multiplier = 1.0
    if bonus_pct > 0.0:
        if direction == "bearish":
            # Bearish convergence should push score lower, away from neutral.
            multiplier = 1.0 - (bonus_pct / 100.0)
        elif direction == "bullish":
            multiplier = 1.0 + (bonus_pct / 100.0)
    quality_penalty_pct = _compute_data_quality_penalty_pct(
        warning_layers=warning_layers,
        stale_layers=stale_layers,
        config=scoring_config,
    )
    quality_multiplier = 1.0 - (quality_penalty_pct / 100.0)
    uncapped_final = weighted * multiplier * quality_multiplier
    final_score = max(0.0, min(100.0, uncapped_final))
    action = resolve_action(
        final_score=final_score,
        thresholds=request.thresholds,
        vetoes=vetoes,
        policy=veto_policy,
    )

    notes = [
        f"active_layers={len(request.layer_scores)}",
        f"bonus_pct={round(bonus_pct, 4)}",
        f"bonus_direction={direction}",
        f"quality_penalty_pct={round(quality_penalty_pct, 4)}",
    ]
    if missing_required:
        notes.append(
            "missing_required_layers="
            + ",".join(layer_id.value for layer_id in missing_required)
        )
    if warning_layers:
        notes.append(
            "warning_layers="
            + ",".join(layer_id.value for layer_id in warning_layers)
        )
    if stale_layers:
        notes.append(
            "stale_layers="
            + ",".join(layer_id.value for layer_id in stale_layers)
        )

    return CompositeResult(
        ticker=request.ticker,
        as_of=request.as_of,
        weighted_score=float(weighted),
        convergence_bonus_pct=float(bonus_pct),
        final_score=float(final_score),
        action=action,
        layer_scores=request.score_map(),
        vetoes=tuple(vetoes or ()),
        notes=tuple(notes),
    )


def score_layer_payloads(
    ticker: str,
    as_of: str,
    layers: Sequence,
    weight_overrides: Optional[Mapping[LayerId, float]] = None,
    external_vetoes: Iterable[str] = (),
    scoring_config: CompositeScoringConfig = CompositeScoringConfig(),
    veto_policy: VetoPolicy = VetoPolicy(),
) -> CompositeResult:
    """Convenience wrapper for scoring a list/tuple of LayerScore payloads."""
    request = CompositeRequest(
        ticker=ticker,
        as_of=as_of,
        layer_scores=tuple(layers),
        weight_overrides=dict(weight_overrides or {}),
    )
    return evaluate_composite(
        request=request,
        external_vetoes=external_vetoes,
        scoring_config=scoring_config,
        veto_policy=veto_policy,
    )
