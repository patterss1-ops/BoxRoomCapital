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
from app.signal.composite import evaluate_composite
from app.signal.contracts import CompositeRequest, LayerScore
from app.signal.types import LAYER_ORDER, LayerId
from data.trade_db import (
    DB_PATH,
    get_research_events,
    load_strategy_state,
    log_event,
    save_strategy_state,
)

STATE_KEY_LAST_REPORT = "signal_shadow:last_report"
DEFAULT_REQUIRED_LAYERS: tuple[LayerId, ...] = (
    LayerId.L1_PEAD,
    LayerId.L2_INSIDER,
    LayerId.L4_ANALYST_REVISIONS,
    LayerId.L8_SA_QUANT,
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


def _clone_layer_score(layer: LayerScore, as_of: str) -> LayerScore:
    return LayerScore(
        layer_id=layer.layer_id,
        ticker=layer.ticker,
        score=layer.score,
        as_of=as_of,
        source=layer.source,
        provenance_ref=layer.provenance_ref,
        confidence=layer.confidence,
        details=dict(layer.details or {}),
    )


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
    now_fn=_utc_now_iso,
) -> dict[str, Any]:
    """Run one shadow composite cycle and persist report into strategy_state."""
    run_at = now_fn()
    run_id = uuid.uuid4().hex[:12]
    required = tuple(required_layers)
    minimum = max(1, int(min_layers_for_score))

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

        request_layers = tuple(
            _clone_layer_score(layer_map[layer_id], as_of=run_at)
            for layer_id in LAYER_ORDER
            if layer_id in layer_map
        )
        composite = evaluate_composite(
            CompositeRequest(
                ticker=ticker,
                as_of=run_at,
                layer_scores=request_layers,
            )
        )
        action = composite.action.value
        action_counts[action] = action_counts.get(action, 0) + 1

        results.append(
            {
                "ticker": ticker,
                "status": "scored",
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
            }
        )

    scored = sum(1 for row in results if row.get("status") == "scored")
    report = {
        "run_id": run_id,
        "run_at": run_at,
        "mode": "shadow",
        "required_layers": [layer_id.value for layer_id in required],
        "summary": {
            "tickers_total": len(universe),
            "tickers_scored": scored,
            "tickers_insufficient_layers": len(universe) - scored,
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
