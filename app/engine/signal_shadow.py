"""Signal Engine shadow-cycle runner and operator snapshot helpers (E-007).

Runs composite scoring in shadow mode from persisted layer score events and
stores the latest report in `strategy_state` for API/UI consumption.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any, Mapping, Optional, Sequence

import config
from app.signal.composite import CompositeScoringConfig, evaluate_composite
from app.signal.contracts import CompositeRequest, LayerScore
from app.signal.decision import VetoPolicy
from app.signal.layer_registry import evaluate_freshness
from app.signal.types import LAYER_ORDER, LayerId
from data.trade_db import (
    DB_PATH,
    get_research_events,
    load_strategy_state,
    log_event,
    save_strategy_state,
)

STATE_KEY_LAST_REPORT = "signal_shadow:last_report"
DATA_QUALITY_VETO_MISSING = "missing_required_layers"
DATA_QUALITY_VETO_STALE = "stale_layer_data"
DEFAULT_REQUIRED_LAYERS: tuple[LayerId, ...] = (
    LayerId.L1_PEAD,
    LayerId.L2_INSIDER,
    LayerId.L4_ANALYST_REVISIONS,
    LayerId.L8_SA_QUANT,
)
BASE_VETO_POLICY = VetoPolicy()
SHADOW_VETO_POLICY = VetoPolicy(
    hard_block_vetoes=tuple(
        dict.fromkeys(
            (
                *BASE_VETO_POLICY.hard_block_vetoes,
                DATA_QUALITY_VETO_MISSING,
                DATA_QUALITY_VETO_STALE,
            )
        )
    ),
    force_short_vetoes=BASE_VETO_POLICY.force_short_vetoes,
)


def _utc_now_iso() -> str:
    """Return RFC3339 UTC timestamp with Z suffix."""
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json_load(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _extract_strategy_tickers() -> list[str]:
    """Read deduped ticker universe from configured strategy slots."""
    tickers: set[str] = set()
    for slot in getattr(config, "STRATEGY_SLOTS", []) or []:
        for raw in slot.get("tickers", []) or []:
            ticker = str(raw or "").strip().upper()
            if ticker:
                tickers.add(ticker)
    return sorted(tickers)


def _parse_layer_score_row(row: Mapping[str, Any]) -> Optional[LayerScore]:
    payload = _safe_json_load(row.get("payload"))
    if not isinstance(payload, dict):
        return None

    try:
        score = LayerScore.from_dict(payload)
    except Exception:
        return None

    symbol = str(row.get("symbol") or "").strip().upper()
    if symbol and symbol != score.ticker:
        adjusted = dict(payload)
        adjusted["ticker"] = symbol
        try:
            score = LayerScore.from_dict(adjusted)
        except Exception:
            pass
    return score


def _collect_latest_layer_scores(
    db_path: str,
    max_events: int = 2000,
) -> dict[str, dict[LayerId, LayerScore]]:
    """Collect latest LayerScore by (ticker, layer_id) from research_events."""
    rows = get_research_events(
        limit=max(1, int(max_events)),
        event_type="signal_layer",
        db_path=db_path,
    )
    by_ticker: dict[str, dict[LayerId, LayerScore]] = {}
    for row in rows:
        score = _parse_layer_score_row(row)
        if not score:
            continue
        layer_map = by_ticker.setdefault(score.ticker, {})
        if score.layer_id not in layer_map:
            layer_map[score.layer_id] = score
    return by_ticker


def _clone_layer_score(layer: LayerScore, as_of: str) -> tuple[LayerScore, str]:
    freshness_state = evaluate_freshness(layer_score=layer, reference_as_of=as_of).value
    details = dict(layer.details or {})
    details["_observed_as_of"] = layer.as_of
    details["_age_hours"] = layer.age_hours(as_of)
    details["_freshness_state"] = freshness_state

    cloned = LayerScore(
        layer_id=layer.layer_id,
        ticker=layer.ticker,
        score=layer.score,
        as_of=as_of,
        source=layer.source,
        provenance_ref=layer.provenance_ref,
        confidence=layer.confidence,
        details=details,
    )
    return cloned, freshness_state


def _build_event_stats(by_ticker: Mapping[str, Mapping[LayerId, LayerScore]]) -> dict[str, Any]:
    layer_coverage = {layer_id.value: 0 for layer_id in LAYER_ORDER}
    freshest_as_of = ""

    for layer_map in by_ticker.values():
        for layer_id, layer in layer_map.items():
            layer_coverage[layer_id.value] = layer_coverage.get(layer_id.value, 0) + 1
            if layer.as_of > freshest_as_of:
                freshest_as_of = layer.as_of

    return {
        "tickers_with_layers": len(by_ticker),
        "layer_coverage": layer_coverage,
        "latest_layer_as_of": freshest_as_of or None,
    }


def run_signal_shadow_cycle(
    db_path: str = DB_PATH,
    required_layers: Sequence[LayerId] = DEFAULT_REQUIRED_LAYERS,
    min_layers_for_score: int = 2,
    enforce_required_layers: bool = False,
    now_fn=_utc_now_iso,
) -> dict[str, Any]:
    """Run one shadow composite cycle and persist report into strategy_state."""
    run_at = now_fn()
    run_id = uuid.uuid4().hex[:12]
    required = tuple(required_layers)
    minimum = max(1, int(min_layers_for_score))
    scoring_config = CompositeScoringConfig(
        required_layers=required,
        warning_layer_penalty_pct=2.5,
        stale_layer_penalty_pct=15.0,
        max_data_quality_penalty_pct=40.0,
        emit_missing_required_veto=bool(enforce_required_layers),
        emit_stale_layer_veto=True,
    )

    by_ticker = _collect_latest_layer_scores(db_path=db_path)
    universe = sorted(set(_extract_strategy_tickers()) | set(by_ticker.keys()))

    results: list[dict[str, Any]] = []
    action_counts = {
        "auto_execute_buy": 0,
        "flag_for_review": 0,
        "short_candidate": 0,
        "no_action": 0,
    }

    for ticker in universe:
        layer_map = by_ticker.get(ticker, {})
        available_layers = [layer_id.value for layer_id in LAYER_ORDER if layer_id in layer_map]
        missing_required = [layer_id.value for layer_id in required if layer_id not in layer_map]

        if len(layer_map) < minimum:
            results.append(
                {
                    "ticker": ticker,
                    "status": "insufficient_layers",
                    "available_layers": available_layers,
                    "missing_required_layers": missing_required,
                    "layer_count": len(layer_map),
                }
            )
            continue

        request_layers: list[LayerScore] = []
        freshness_by_layer: dict[str, str] = {}
        warning_layers: list[str] = []
        stale_layers: list[str] = []
        for layer_id in LAYER_ORDER:
            layer = layer_map.get(layer_id)
            if not layer:
                continue
            cloned, freshness_state = _clone_layer_score(layer, as_of=run_at)
            request_layers.append(cloned)
            freshness_by_layer[layer_id.value] = freshness_state
            if freshness_state == "warning":
                warning_layers.append(layer_id.value)
            elif freshness_state == "stale":
                stale_layers.append(layer_id.value)

        external_vetoes: list[str] = []
        if missing_required and bool(enforce_required_layers):
            external_vetoes.append(DATA_QUALITY_VETO_MISSING)
        if stale_layers:
            external_vetoes.append(DATA_QUALITY_VETO_STALE)

        composite = evaluate_composite(
            CompositeRequest(
                ticker=ticker,
                as_of=run_at,
                layer_scores=tuple(request_layers),
            ),
            external_vetoes=tuple(external_vetoes),
            scoring_config=scoring_config,
            veto_policy=SHADOW_VETO_POLICY,
        )
        action = composite.action.value
        action_counts[action] = action_counts.get(action, 0) + 1
        status = "scored"
        vetoes = set(composite.vetoes)
        if DATA_QUALITY_VETO_MISSING in vetoes:
            status = "blocked_missing_required_layers"
        elif DATA_QUALITY_VETO_STALE in vetoes:
            status = "blocked_stale_layers"
        elif warning_layers:
            status = "scored_warning_layers"

        results.append(
            {
                "ticker": ticker,
                "status": status,
                "available_layers": available_layers,
                "missing_required_layers": missing_required,
                "layer_count": len(request_layers),
                "weighted_score": composite.weighted_score,
                "convergence_bonus_pct": composite.convergence_bonus_pct,
                "final_score": composite.final_score,
                "action": action,
                "vetoes": list(composite.vetoes),
                "notes": list(composite.notes),
                "layer_scores": composite.to_dict().get("layer_scores", {}),
                "freshness": {
                    "layer_states": freshness_by_layer,
                    "warning_layers": warning_layers,
                    "stale_layers": stale_layers,
                },
            }
        )

    scored = sum(
        1 for row in results if str(row.get("status") or "").startswith("scored")
    )
    insufficient = sum(1 for row in results if row.get("status") == "insufficient_layers")
    scored_missing_required = sum(
        1
        for row in results
        if str(row.get("status") or "").startswith("scored")
        and bool(row.get("missing_required_layers"))
    )
    blocked_missing = sum(
        1 for row in results if row.get("status") == "blocked_missing_required_layers"
    )
    blocked_stale = sum(1 for row in results if row.get("status") == "blocked_stale_layers")
    report = {
        "run_id": run_id,
        "run_at": run_at,
        "mode": "shadow",
        "required_layers": [layer_id.value for layer_id in required],
        "summary": {
            "tickers_total": len(universe),
            "tickers_scored": scored,
            "tickers_insufficient_layers": insufficient,
            "tickers_scored_missing_required_layers": scored_missing_required,
            "tickers_blocked_missing_required_layers": blocked_missing,
            "tickers_blocked_stale_layers": blocked_stale,
            "action_counts": action_counts,
        },
        "results": results,
    }

    save_strategy_state(
        key=STATE_KEY_LAST_REPORT,
        value=json.dumps(report, sort_keys=True),
        db_path=db_path,
    )

    try:
        log_event(
            category="SIGNAL",
            headline="Signal shadow cycle completed",
            detail=(
                f"run_id={run_id}, scored={scored}/{len(universe)}, "
                f"auto={action_counts.get('auto_execute_buy', 0)}, "
                f"short={action_counts.get('short_candidate', 0)}"
            ),
            strategy="signal_engine",
            db_path=db_path,
        )
    except Exception:
        # Shadow cycle output is persisted; event logging failure is non-fatal.
        pass

    return report


def get_signal_shadow_report(
    db_path: str = DB_PATH,
    max_events: int = 2000,
) -> dict[str, Any]:
    """Return latest persisted shadow report + live layer coverage stats."""
    by_ticker = _collect_latest_layer_scores(db_path=db_path, max_events=max_events)
    event_stats = _build_event_stats(by_ticker)
    raw = load_strategy_state(STATE_KEY_LAST_REPORT, db_path=db_path)

    if not raw:
        return {
            "ok": True,
            "state": "idle",
            "has_report": False,
            "report": None,
            "event_stats": event_stats,
        }

    report = _safe_json_load(raw)
    if not isinstance(report, dict):
        return {
            "ok": False,
            "state": "error",
            "has_report": False,
            "report": None,
            "event_stats": event_stats,
            "error": "invalid_report_payload",
        }

    return {
        "ok": True,
        "state": "ready",
        "has_report": True,
        "report": report,
        "event_stats": event_stats,
    }
