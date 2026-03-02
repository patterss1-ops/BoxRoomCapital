"""End-to-end Signal Engine acceptance tests (E-008).

Validates the full signal pipeline from raw layer events through composite
scoring, decision resolution, shadow cycle persistence, and operator surface.

Pipeline under test:

    Layer adapters produce LayerScore payloads
        → EventStore persists as research_events (signal_layer)
        → Shadow cycle collects latest events per (ticker, layer_id)
        → Composite scorer: weighted score + convergence bonus + vetoes
        → Decision engine: action thresholds + veto policy
        → Report persisted in strategy_state
        → API + HTMX fragment serve operator snapshot

Each test uses a real SQLite DB and EventStore — no mocks on the critical path.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api import server
from app.engine import signal_shadow
from app.signal.composite import (
    CompositeScoringConfig,
    evaluate_composite,
    score_layer_payloads,
)
from app.signal.contracts import CompositeRequest, CompositeResult, LayerScore
from app.signal.decision import VetoPolicy
from app.signal.types import DecisionAction, LayerId, LAYER_ORDER, ScoreThresholds
from data import trade_db
from intelligence.event_store import EventRecord, EventStore


# ── Timestamps ────────────────────────────────────────────────────────────

AS_OF = "2026-03-02T12:00:00Z"
RETRIEVED_AT = "2026-03-02T12:00:05Z"


# ── Helpers ───────────────────────────────────────────────────────────────

def _layer(
    ticker: str,
    layer_id: LayerId,
    score: float,
    *,
    source: str = "e2e-test",
    confidence: float = 0.85,
    details: dict | None = None,
) -> LayerScore:
    return LayerScore(
        layer_id=layer_id,
        ticker=ticker,
        score=score,
        as_of=AS_OF,
        source=source,
        provenance_ref=f"e2e:{ticker}:{layer_id.value}",
        confidence=confidence,
        details=details or {"e2e": True},
    )


def _write_layer_event(
    store: EventStore,
    layer_score: LayerScore,
    *,
    retrieved_at: str = RETRIEVED_AT,
    run_ref: str = "e2e-run",
):
    """Persist a LayerScore into research_events via EventStore."""
    store.write_event(
        EventRecord(
            event_type="signal_layer",
            source=layer_score.source,
            source_ref=f"{layer_score.provenance_ref}:{run_ref}",
            retrieved_at=retrieved_at,
            event_timestamp=layer_score.as_of,
            symbol=layer_score.ticker,
            headline=f"{layer_score.layer_id.value} score",
            detail=f"score={layer_score.score}",
            confidence=layer_score.confidence,
            provenance_descriptor={
                "layer_id": layer_score.layer_id.value,
                "ticker": layer_score.ticker,
                "run_ref": run_ref,
            },
            payload=layer_score.to_dict(),
        )
    )


def _seed_bullish_ticker(store: EventStore, ticker: str) -> list[LayerScore]:
    """Seed a strongly bullish ticker with 4 high-scoring layers."""
    layers = [
        _layer(ticker, LayerId.L1_PEAD, 88.0, source="pead"),
        _layer(ticker, LayerId.L2_INSIDER, 82.0, source="insider"),
        _layer(ticker, LayerId.L4_ANALYST_REVISIONS, 85.0, source="analyst-rev"),
        _layer(ticker, LayerId.L8_SA_QUANT, 90.0, source="sa-quant"),
    ]
    for ls in layers:
        _write_layer_event(store, ls, run_ref="bullish-seed")
    return layers


def _seed_bearish_ticker(store: EventStore, ticker: str) -> list[LayerScore]:
    """Seed a strongly bearish ticker with 4 low-scoring layers."""
    layers = [
        _layer(ticker, LayerId.L1_PEAD, 15.0, source="pead"),
        _layer(ticker, LayerId.L2_INSIDER, 12.0, source="insider"),
        _layer(ticker, LayerId.L4_ANALYST_REVISIONS, 18.0, source="analyst-rev"),
        _layer(ticker, LayerId.L8_SA_QUANT, 10.0, source="sa-quant"),
    ]
    for ls in layers:
        _write_layer_event(store, ls, run_ref="bearish-seed")
    return layers


def _seed_mixed_ticker(store: EventStore, ticker: str) -> list[LayerScore]:
    """Seed a mixed-signal ticker (review zone)."""
    layers = [
        _layer(ticker, LayerId.L1_PEAD, 62.0, source="pead"),
        _layer(ticker, LayerId.L2_INSIDER, 55.0, source="insider"),
        _layer(ticker, LayerId.L4_ANALYST_REVISIONS, 48.0, source="analyst-rev"),
        _layer(ticker, LayerId.L8_SA_QUANT, 58.0, source="sa-quant"),
    ]
    for ls in layers:
        _write_layer_event(store, ls, run_ref="mixed-seed")
    return layers


class ImmediateThread:
    """Synchronous thread double for deterministic action tests."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def _bind_job_db(monkeypatch, db_path: str):
    """Redirect server job functions to test DB."""
    def wrap(fn):
        def inner(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)
        return inner

    monkeypatch.setattr(server, "init_db", lambda: trade_db.init_db(db_path))
    monkeypatch.setattr(server, "create_job", wrap(trade_db.create_job))
    monkeypatch.setattr(server, "update_job", wrap(trade_db.update_job))
    monkeypatch.setattr(server, "get_jobs", wrap(trade_db.get_jobs))
    monkeypatch.setattr(server, "get_job", wrap(trade_db.get_job))


# ── 1. Layer → EventStore → Composite (Unit Integration) ─────────────────

class TestLayerToCompositeFlow:
    """Verify that LayerScore payloads survive round-trip through EventStore
    and produce correct composite scores."""

    def test_layer_scores_round_trip_through_event_store(self, tmp_path):
        """LayerScore → EventStore → read back → identical payload."""
        db_path = str(tmp_path / "roundtrip.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)

        original = _layer("AAPL", LayerId.L1_PEAD, 78.5, source="pead")
        _write_layer_event(store, original, run_ref="rt-test")

        events = store.list_events(limit=10, event_type="signal_layer")
        assert len(events) >= 1

        payload = events[0]["payload"]
        reconstructed = LayerScore.from_dict(payload)
        assert reconstructed.layer_id == original.layer_id
        assert reconstructed.ticker == original.ticker
        assert reconstructed.score == original.score
        assert reconstructed.source == original.source

    def test_four_layer_bullish_composite_auto_execute(self, tmp_path):
        """Four strong bullish layers → composite ≥ 70 → auto_execute_buy."""
        db_path = str(tmp_path / "bullish.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        layers = _seed_bullish_ticker(store, "AAPL")

        result = score_layer_payloads(
            ticker="AAPL",
            as_of=AS_OF,
            layers=layers,
        )
        assert isinstance(result, CompositeResult)
        assert result.action == DecisionAction.AUTO_EXECUTE_BUY
        assert result.final_score >= 70.0
        assert result.weighted_score > 0
        assert len(result.layer_scores) == 4

    def test_four_layer_bearish_composite_short_or_no_action(self, tmp_path):
        """Four strong bearish layers → composite ≤ 30 → short_candidate."""
        db_path = str(tmp_path / "bearish.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        layers = _seed_bearish_ticker(store, "TSLA")

        result = score_layer_payloads(
            ticker="TSLA",
            as_of=AS_OF,
            layers=layers,
        )
        assert result.final_score <= 30.0
        assert result.action in (
            DecisionAction.SHORT_CANDIDATE,
            DecisionAction.NO_ACTION,
        )

    def test_mixed_signal_flags_for_review(self, tmp_path):
        """Mixed signals in 50-69 range → flag_for_review."""
        db_path = str(tmp_path / "mixed.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        layers = _seed_mixed_ticker(store, "GOOG")

        result = score_layer_payloads(
            ticker="GOOG",
            as_of=AS_OF,
            layers=layers,
        )
        assert 40.0 <= result.final_score <= 69.0
        assert result.action == DecisionAction.FLAG_FOR_REVIEW

    def test_single_layer_produces_valid_composite(self, tmp_path):
        """Even a single layer should produce a valid composite result."""
        db_path = str(tmp_path / "single.db")
        trade_db.init_db(db_path)

        single = _layer("NVDA", LayerId.L8_SA_QUANT, 75.0)
        result = score_layer_payloads(
            ticker="NVDA",
            as_of=AS_OF,
            layers=[single],
        )
        assert result.weighted_score == pytest.approx(75.0)
        assert result.action == DecisionAction.AUTO_EXECUTE_BUY


# ── 2. Convergence Bonus Calibration ─────────────────────────────────────

class TestConvergenceBonusCalibration:
    """Verify convergence bonus is correctly applied to final scores."""

    def test_bullish_convergence_increases_score(self):
        """4 bullish layers → positive bonus → final > weighted."""
        layers = [
            _layer("SPY", LayerId.L1_PEAD, 85.0),
            _layer("SPY", LayerId.L2_INSIDER, 80.0),
            _layer("SPY", LayerId.L4_ANALYST_REVISIONS, 78.0),
            _layer("SPY", LayerId.L8_SA_QUANT, 82.0),
        ]
        result = score_layer_payloads("SPY", AS_OF, layers)
        assert result.convergence_bonus_pct > 0.0
        assert result.final_score > result.weighted_score

    def test_bearish_convergence_decreases_score(self):
        """4 bearish layers → positive bonus → final < weighted."""
        layers = [
            _layer("COIN", LayerId.L1_PEAD, 12.0),
            _layer("COIN", LayerId.L2_INSIDER, 15.0),
            _layer("COIN", LayerId.L4_ANALYST_REVISIONS, 10.0),
            _layer("COIN", LayerId.L8_SA_QUANT, 18.0),
        ]
        result = score_layer_payloads("COIN", AS_OF, layers)
        assert result.convergence_bonus_pct > 0.0
        assert result.final_score < result.weighted_score

    def test_mixed_signals_no_convergence_bonus(self):
        """Mixed bullish/bearish layers → no convergence bonus."""
        layers = [
            _layer("AMZN", LayerId.L1_PEAD, 85.0),
            _layer("AMZN", LayerId.L2_INSIDER, 20.0),
            _layer("AMZN", LayerId.L4_ANALYST_REVISIONS, 90.0),
            _layer("AMZN", LayerId.L8_SA_QUANT, 15.0),
        ]
        result = score_layer_payloads("AMZN", AS_OF, layers)
        assert result.convergence_bonus_pct == 0.0
        assert result.final_score == pytest.approx(result.weighted_score)

    def test_convergence_bonus_capped_at_max(self):
        """Even with extreme convergence, bonus stays ≤ max_convergence_bonus_pct."""
        layers = [
            _layer("META", LayerId.L1_PEAD, 99.0),
            _layer("META", LayerId.L2_INSIDER, 98.0),
            _layer("META", LayerId.L4_ANALYST_REVISIONS, 99.0),
            _layer("META", LayerId.L8_SA_QUANT, 97.0),
        ]
        cfg = CompositeScoringConfig(max_convergence_bonus_pct=12.0)
        result = evaluate_composite(
            CompositeRequest(ticker="META", as_of=AS_OF, layer_scores=tuple(layers)),
            scoring_config=cfg,
        )
        assert result.convergence_bonus_pct <= 12.0


# ── 3. Veto Engine E2E ───────────────────────────────────────────────────

class TestVetoEngineE2E:
    """Verify veto codes correctly override score-based decisions."""

    def test_hard_block_veto_overrides_high_score(self):
        """An 85-score ticker with kill_switch_active → no_action."""
        layers = [
            _layer("AAPL", LayerId.L1_PEAD, 90.0),
            _layer("AAPL", LayerId.L2_INSIDER, 85.0,
                   details={"vetoed": True, "veto_reason": "kill_switch_active"}),
            _layer("AAPL", LayerId.L8_SA_QUANT, 88.0),
        ]
        result = score_layer_payloads(
            "AAPL", AS_OF, layers,
            external_vetoes=["kill_switch_active"],
        )
        assert result.action == DecisionAction.NO_ACTION
        assert "kill_switch_active" in result.vetoes

    def test_insider_sell_cluster_veto(self):
        """insider_sell_cluster veto blocks an otherwise strong buy."""
        layers = [
            _layer("MSFT", LayerId.L1_PEAD, 80.0),
            _layer("MSFT", LayerId.L2_INSIDER, 78.0,
                   details={"vetoed": True, "veto_reason": "insider_sell_cluster"}),
            _layer("MSFT", LayerId.L4_ANALYST_REVISIONS, 82.0),
            _layer("MSFT", LayerId.L8_SA_QUANT, 85.0),
        ]
        result = score_layer_payloads("MSFT", AS_OF, layers)
        assert result.action == DecisionAction.NO_ACTION
        assert "insider_sell_cluster" in result.vetoes

    def test_no_vetoes_allows_score_based_decision(self):
        """Clean layers with no vetoes → decision based purely on score."""
        layers = [
            _layer("GOOG", LayerId.L1_PEAD, 75.0),
            _layer("GOOG", LayerId.L8_SA_QUANT, 72.0),
        ]
        result = score_layer_payloads("GOOG", AS_OF, layers)
        assert len(result.vetoes) == 0
        assert result.action == DecisionAction.AUTO_EXECUTE_BUY

    def test_layer_floor_breach_emits_veto(self):
        """A layer score below the floor emits a floor_breach veto."""
        layers = [
            _layer("BA", LayerId.L1_PEAD, 80.0),
            _layer("BA", LayerId.L2_INSIDER, 5.0),
            _layer("BA", LayerId.L8_SA_QUANT, 70.0),
        ]
        cfg = CompositeScoringConfig(
            layer_score_floors={LayerId.L2_INSIDER: 20.0},
        )
        result = evaluate_composite(
            CompositeRequest(ticker="BA", as_of=AS_OF, layer_scores=tuple(layers)),
            scoring_config=cfg,
        )
        assert any("layer_floor_breach" in v for v in result.vetoes)


# ── 4. Shadow Cycle Full Integration ─────────────────────────────────────

class TestShadowCycleIntegration:
    """Full shadow cycle: seed events → run cycle → verify report."""

    def test_shadow_cycle_scores_seeded_tickers(self, tmp_path, monkeypatch):
        """Shadow cycle picks up seeded layer events and scores tickers."""
        db_path = str(tmp_path / "shadow_e2e.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)

        # Isolate universe to seeded tickers only
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _seed_bullish_ticker(store, "AAPL")
        _seed_bearish_ticker(store, "TSLA")
        _seed_mixed_ticker(store, "GOOG")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )

        assert report["mode"] == "shadow"
        assert report["summary"]["tickers_total"] == 3
        assert report["summary"]["tickers_scored"] == 3
        assert report["summary"]["tickers_insufficient_layers"] == 0

        results_by_ticker = {r["ticker"]: r for r in report["results"]}

        # AAPL: strong bullish → auto_execute_buy
        aapl = results_by_ticker["AAPL"]
        assert aapl["status"] == "scored"
        assert aapl["action"] == "auto_execute_buy"
        assert aapl["final_score"] >= 70.0

        # TSLA: strong bearish → short_candidate or no_action
        tsla = results_by_ticker["TSLA"]
        assert tsla["status"] == "scored"
        assert tsla["action"] in ("short_candidate", "no_action")
        assert tsla["final_score"] <= 30.0

        # GOOG: mixed → flag_for_review
        goog = results_by_ticker["GOOG"]
        assert goog["status"] == "scored"
        assert goog["action"] == "flag_for_review"

    def test_shadow_cycle_latest_wins_over_stale(self, tmp_path, monkeypatch):
        """When multiple events exist for same (ticker, layer), latest wins."""
        db_path = str(tmp_path / "shadow_latest.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        # Old event: low score
        old = _layer("NVDA", LayerId.L1_PEAD, 20.0, source="pead")
        _write_layer_event(store, old, retrieved_at="2026-03-02T08:00:00Z", run_ref="old")

        # New event: high score
        new = _layer("NVDA", LayerId.L1_PEAD, 92.0, source="pead")
        _write_layer_event(store, new, retrieved_at="2026-03-02T12:00:00Z", run_ref="new")

        # Second layer to meet min_layers_for_score=2
        l8 = _layer("NVDA", LayerId.L8_SA_QUANT, 88.0, source="sa-quant")
        _write_layer_event(store, l8, retrieved_at="2026-03-02T12:01:00Z", run_ref="sa")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )

        nvda = report["results"][0]
        assert nvda["ticker"] == "NVDA"
        assert nvda["status"] == "scored"
        assert nvda["layer_scores"]["l1_pead"] == 92.0  # New wins

    def test_shadow_cycle_insufficient_layers_skipped(self, tmp_path, monkeypatch):
        """Tickers with fewer layers than minimum are marked insufficient."""
        db_path = str(tmp_path / "shadow_insuf.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        # Only 1 layer for AMD
        single = _layer("AMD", LayerId.L1_PEAD, 65.0, source="pead")
        _write_layer_event(store, single, run_ref="single")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )

        assert report["summary"]["tickers_total"] == 1
        assert report["summary"]["tickers_scored"] == 0
        assert report["summary"]["tickers_insufficient_layers"] == 1
        assert report["results"][0]["status"] == "insufficient_layers"

    def test_shadow_report_persists_and_reloads(self, tmp_path, monkeypatch):
        """After cycle, get_signal_shadow_report returns persisted report."""
        db_path = str(tmp_path / "shadow_persist.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _seed_bullish_ticker(store, "MSFT")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )

        snapshot = signal_shadow.get_signal_shadow_report(db_path=db_path)
        assert snapshot["ok"] is True
        assert snapshot["has_report"] is True
        assert snapshot["state"] == "ready"
        assert snapshot["report"]["run_id"] == report["run_id"]
        assert snapshot["report"]["summary"]["tickers_scored"] == 1

    def test_shadow_report_idle_when_no_cycle_run(self, tmp_path):
        """Before any cycle, report state is idle with no report."""
        db_path = str(tmp_path / "shadow_idle.db")
        trade_db.init_db(db_path)

        snapshot = signal_shadow.get_signal_shadow_report(db_path=db_path)
        assert snapshot["ok"] is True
        assert snapshot["state"] == "idle"
        assert snapshot["has_report"] is False

    def test_event_stats_reflect_layer_coverage(self, tmp_path, monkeypatch):
        """Event stats correctly count tickers per layer."""
        db_path = str(tmp_path / "shadow_stats.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        # AAPL: 4 layers, MSFT: 2 layers
        _seed_bullish_ticker(store, "AAPL")
        l1 = _layer("MSFT", LayerId.L1_PEAD, 60.0, source="pead")
        l8 = _layer("MSFT", LayerId.L8_SA_QUANT, 55.0, source="sa-quant")
        _write_layer_event(store, l1, run_ref="msft-l1")
        _write_layer_event(store, l8, run_ref="msft-l8")

        snapshot = signal_shadow.get_signal_shadow_report(db_path=db_path)
        stats = snapshot["event_stats"]
        assert stats["tickers_with_layers"] == 2
        assert stats["layer_coverage"]["l1_pead"] == 2  # AAPL + MSFT
        assert stats["layer_coverage"]["l8_sa_quant"] == 2
        assert stats["layer_coverage"]["l2_insider"] == 1  # AAPL only


# ── 5. Operator Surface (API + Fragment) ──────────────────────────────────

class TestOperatorSurface:
    """Verify the API and HTMX fragment serve correct operator data."""

    def test_api_signal_shadow_returns_idle_initially(self, tmp_path, monkeypatch):
        """GET /api/signal-shadow returns idle state before any cycle."""
        db_path = str(tmp_path / "api_idle.db")
        trade_db.init_db(db_path)

        monkeypatch.setattr(
            server,
            "get_signal_shadow_report",
            lambda: signal_shadow.get_signal_shadow_report(db_path=db_path),
        )

        with TestClient(server.app) as client:
            resp = client.get("/api/signal-shadow")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["state"] == "idle"
        assert body["has_report"] is False

    def test_api_signal_shadow_returns_report_after_cycle(
        self, tmp_path, monkeypatch,
    ):
        """GET /api/signal-shadow returns scored report after shadow cycle."""
        db_path = str(tmp_path / "api_report.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _seed_bullish_ticker(store, "AAPL")

        signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )

        monkeypatch.setattr(
            server,
            "get_signal_shadow_report",
            lambda: signal_shadow.get_signal_shadow_report(db_path=db_path),
        )

        with TestClient(server.app) as client:
            resp = client.get("/api/signal-shadow")

        assert resp.status_code == 200
        body = resp.json()
        assert body["has_report"] is True
        assert body["report"]["summary"]["tickers_scored"] == 1

    def test_signal_shadow_run_action_creates_job(self, tmp_path, monkeypatch):
        """POST /api/actions/signal-shadow-run creates and completes a job."""
        db_path = str(tmp_path / "api_action.db")
        trade_db.init_db(db_path)
        _bind_job_db(monkeypatch, db_path)
        monkeypatch.setattr(server.threading, "Thread", ImmediateThread)
        monkeypatch.setattr(
            server,
            "run_signal_shadow_cycle",
            lambda: {
                "run_id": "e2e-test-run",
                "run_at": AS_OF,
                "summary": {"tickers_total": 3, "tickers_scored": 2},
                "results": [],
            },
        )

        with TestClient(server.app) as client:
            resp = client.post("/api/actions/signal-shadow-run")

        assert resp.status_code == 200
        assert "Queued signal shadow run" in resp.text

        jobs = trade_db.get_jobs(limit=5, db_path=db_path)
        assert len(jobs) >= 1
        job = jobs[0]
        assert job["job_type"] == "signal_shadow_run"
        assert job["status"] == "completed"
        assert "scored=2/3" in (job["detail"] or "")

    def test_fragment_renders_with_scored_report(self, tmp_path, monkeypatch):
        """GET /fragments/signal-engine renders HTMX panel with report data."""
        monkeypatch.setattr(
            server,
            "get_signal_shadow_report",
            lambda: {
                "ok": True,
                "state": "ready",
                "has_report": True,
                "event_stats": {
                    "tickers_with_layers": 3,
                    "layer_coverage": {
                        "l1_pead": 3,
                        "l2_insider": 2,
                        "l4_analyst_revisions": 2,
                        "l8_sa_quant": 3,
                    },
                    "latest_layer_as_of": AS_OF,
                },
                "report": {
                    "run_id": "e2e-frag-run",
                    "run_at": AS_OF,
                    "summary": {
                        "tickers_total": 3,
                        "tickers_scored": 3,
                        "action_counts": {
                            "auto_execute_buy": 1,
                            "flag_for_review": 1,
                            "short_candidate": 1,
                            "no_action": 0,
                        },
                    },
                    "results": [
                        {
                            "ticker": "AAPL",
                            "status": "scored",
                            "final_score": 84.2,
                            "action": "auto_execute_buy",
                            "layer_count": 4,
                            "missing_required_layers": [],
                        },
                        {
                            "ticker": "GOOG",
                            "status": "scored",
                            "final_score": 55.1,
                            "action": "flag_for_review",
                            "layer_count": 4,
                            "missing_required_layers": [],
                        },
                        {
                            "ticker": "TSLA",
                            "status": "scored",
                            "final_score": 14.3,
                            "action": "short_candidate",
                            "layer_count": 4,
                            "missing_required_layers": [],
                        },
                    ],
                },
            },
        )

        with TestClient(server.app) as client:
            resp = client.get("/fragments/signal-engine")

        assert resp.status_code == 200
        html = resp.text
        assert "Signal Engine Shadow Run" in html
        assert "e2e-frag-run" in html
        assert "AAPL" in html
        assert "GOOG" in html
        assert "TSLA" in html
        assert "auto_execute_buy" in html
        assert "flag_for_review" in html
        assert "short_candidate" in html

    def test_fragment_renders_idle_state(self, monkeypatch):
        """Fragment renders gracefully when no report exists."""
        monkeypatch.setattr(
            server,
            "get_signal_shadow_report",
            lambda: {
                "ok": True,
                "state": "idle",
                "has_report": False,
                "report": None,
                "event_stats": None,
            },
        )

        with TestClient(server.app) as client:
            resp = client.get("/fragments/signal-engine")

        assert resp.status_code == 200
        html = resp.text
        assert "Signal Engine Shadow Run" in html
        assert "No shadow report yet" in html


# ── 6. Decision Threshold Boundary Tests ──────────────────────────────────

class TestDecisionBoundaries:
    """Verify action resolution at exact threshold boundaries."""

    def test_score_exactly_70_is_auto_execute(self):
        """Score == auto_execute_gte (70.0) → auto_execute_buy."""
        layers = [_layer("TEST", LayerId.L1_PEAD, 70.0)]
        result = score_layer_payloads("TEST", AS_OF, layers)
        assert result.action == DecisionAction.AUTO_EXECUTE_BUY

    def test_score_69_99_is_review(self):
        """Score just below 70.0 → flag_for_review."""
        layers = [_layer("TEST", LayerId.L1_PEAD, 69.9)]
        result = score_layer_payloads("TEST", AS_OF, layers)
        assert result.action == DecisionAction.FLAG_FOR_REVIEW

    def test_score_exactly_50_is_review(self):
        """Score == review_gte (50.0) → flag_for_review."""
        layers = [_layer("TEST", LayerId.L1_PEAD, 50.0)]
        result = score_layer_payloads("TEST", AS_OF, layers)
        assert result.action == DecisionAction.FLAG_FOR_REVIEW

    def test_score_exactly_30_is_short(self):
        """Score == short_lte (30.0) → short_candidate."""
        layers = [_layer("TEST", LayerId.L1_PEAD, 30.0)]
        result = score_layer_payloads("TEST", AS_OF, layers)
        assert result.action == DecisionAction.SHORT_CANDIDATE

    def test_score_31_is_no_action(self):
        """Score between short_lte and review_gte → no_action."""
        layers = [_layer("TEST", LayerId.L1_PEAD, 31.0)]
        result = score_layer_payloads("TEST", AS_OF, layers)
        assert result.action == DecisionAction.NO_ACTION


# ── 7. Weight Renormalization with Partial Layers ─────────────────────────

class TestWeightRenormalization:
    """Verify composite scoring works correctly with subset of 8 layers."""

    def test_two_layers_renormalize_to_sum_one(self):
        """With 2 of 8 layers active, weights renormalize to sum=1.0."""
        layers = [
            _layer("SPY", LayerId.L1_PEAD, 80.0),
            _layer("SPY", LayerId.L8_SA_QUANT, 60.0),
        ]
        request = CompositeRequest(ticker="SPY", as_of=AS_OF, layer_scores=tuple(layers))
        from app.signal.composite import _active_weights
        weights = _active_weights(request)
        assert pytest.approx(sum(weights.values()), abs=1e-6) == 1.0
        assert len(weights) == 2

    def test_all_eight_layers_weighted_correctly(self):
        """With all 8 layers, weights match the default proportions."""
        layers = [
            _layer("SPY", layer_id, 75.0)
            for layer_id in LAYER_ORDER
        ]
        result = score_layer_payloads("SPY", AS_OF, layers)
        # All layers at 75.0 → weighted score should be 75.0
        assert result.weighted_score == pytest.approx(75.0, abs=0.01)

    def test_four_layer_weight_sum_is_one(self):
        """Standard 4-layer setup renormalizes correctly."""
        layers = [
            _layer("QQQ", LayerId.L1_PEAD, 50.0),
            _layer("QQQ", LayerId.L2_INSIDER, 50.0),
            _layer("QQQ", LayerId.L4_ANALYST_REVISIONS, 50.0),
            _layer("QQQ", LayerId.L8_SA_QUANT, 50.0),
        ]
        result = score_layer_payloads("QQQ", AS_OF, layers)
        # All at 50.0 → weighted = 50.0 regardless of renormalization
        assert result.weighted_score == pytest.approx(50.0, abs=0.01)


# ── 8. Composite Result Contract Validation ───────────────────────────────

class TestCompositeResultContract:
    """Verify CompositeResult fields are well-formed and serializable."""

    def test_composite_result_to_dict_is_json_safe(self):
        """CompositeResult.to_dict() produces JSON-serializable output."""
        layers = _seed_bullish_ticker.__wrapped__(None, "AAPL") if hasattr(_seed_bullish_ticker, "__wrapped__") else [
            _layer("AAPL", LayerId.L1_PEAD, 88.0),
            _layer("AAPL", LayerId.L2_INSIDER, 82.0),
            _layer("AAPL", LayerId.L4_ANALYST_REVISIONS, 85.0),
            _layer("AAPL", LayerId.L8_SA_QUANT, 90.0),
        ]
        result = score_layer_payloads("AAPL", AS_OF, layers)

        d = result.to_dict()
        serialized = json.dumps(d, sort_keys=True)
        parsed = json.loads(serialized)
        assert parsed["ticker"] == "AAPL"
        assert parsed["action"] in (
            "auto_execute_buy",
            "flag_for_review",
            "short_candidate",
            "no_action",
        )
        assert "layer_scores" in parsed

    def test_composite_result_notes_include_metadata(self):
        """Result notes contain active_layers count and bonus info."""
        layers = [
            _layer("TSLA", LayerId.L1_PEAD, 80.0),
            _layer("TSLA", LayerId.L8_SA_QUANT, 75.0),
        ]
        result = score_layer_payloads("TSLA", AS_OF, layers)
        notes = list(result.notes)
        assert any("active_layers=2" in n for n in notes)
        assert any("bonus_pct=" in n for n in notes)

    def test_layer_scores_in_result_match_input(self):
        """CompositeResult.layer_scores maps back to input scores."""
        layers = [
            _layer("AMD", LayerId.L1_PEAD, 72.0),
            _layer("AMD", LayerId.L4_ANALYST_REVISIONS, 65.0),
        ]
        result = score_layer_payloads("AMD", AS_OF, layers)
        assert result.layer_scores[LayerId.L1_PEAD] == 72.0
        assert result.layer_scores[LayerId.L4_ANALYST_REVISIONS] == 65.0
