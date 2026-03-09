"""End-to-end Signal Engine acceptance tests (E-008 + F-008).

Validates the full signal pipeline from raw layer events through composite
scoring, decision resolution, shadow cycle persistence, and operator surface.

Pipeline under test:

    Layer adapters produce LayerScore payloads
        → EventStore persists as research_events (signal_layer)
        → Shadow cycle collects latest events per (ticker, layer_id)
        → Composite scorer: weighted score + convergence bonus + vetoes
        → Decision engine: action thresholds + veto policy
        → Data quality penalties: warning/stale freshness enforcement (F-006)
        → Ranked candidates: directional sort for operator surface (F-007)
        → Tier-1 job orchestration: SA Quant refresh + shadow cycle (F-007)
        → Report persisted in strategy_state
        → API + HTMX fragment serve operator snapshot

Sections 1-8: Original E-008 tests (4-layer scenarios)
Sections 9-15: F-008 extensions (full tier-1 L1-L8 + F-006/F-007 features)

Each test uses a real SQLite DB and EventStore — no mocks on the critical path.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

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
from intelligence.jobs.signal_layer_jobs import (
    build_ranked_candidates,
    enrich_signal_shadow_payload,
    summarize_freshness_diagnostics,
    Tier1ShadowJobsConfig,
)
from tests.asgi_client import ASGITestClient


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


# ── Full Tier-1 (8-layer) Seed Helpers (F-008) ──────────────────────────

def _seed_bullish_full_tier1(store: EventStore, ticker: str) -> list[LayerScore]:
    """Seed a strongly bullish ticker with all 8 signal layers."""
    layers = [
        _layer(ticker, LayerId.L1_PEAD, 88.0, source="pead"),
        _layer(ticker, LayerId.L2_INSIDER, 82.0, source="insider"),
        _layer(ticker, LayerId.L3_SHORT_INTEREST, 79.0, source="finra-si"),
        _layer(ticker, LayerId.L4_ANALYST_REVISIONS, 85.0, source="analyst-rev"),
        _layer(ticker, LayerId.L5_CONGRESSIONAL, 76.0, source="capitol-trades"),
        _layer(ticker, LayerId.L6_NEWS_SENTIMENT, 80.0, source="news-sentiment"),
        _layer(ticker, LayerId.L7_TECHNICAL, 73.0, source="technical"),
        _layer(ticker, LayerId.L8_SA_QUANT, 90.0, source="sa-quant"),
    ]
    for ls in layers:
        _write_layer_event(store, ls, run_ref="bullish-full-seed")
    return layers


def _seed_bearish_full_tier1(store: EventStore, ticker: str) -> list[LayerScore]:
    """Seed a strongly bearish ticker with all 8 signal layers."""
    layers = [
        _layer(ticker, LayerId.L1_PEAD, 15.0, source="pead"),
        _layer(ticker, LayerId.L2_INSIDER, 12.0, source="insider"),
        _layer(ticker, LayerId.L3_SHORT_INTEREST, 20.0, source="finra-si"),
        _layer(ticker, LayerId.L4_ANALYST_REVISIONS, 18.0, source="analyst-rev"),
        _layer(ticker, LayerId.L5_CONGRESSIONAL, 22.0, source="capitol-trades"),
        _layer(ticker, LayerId.L6_NEWS_SENTIMENT, 14.0, source="news-sentiment"),
        _layer(ticker, LayerId.L7_TECHNICAL, 25.0, source="technical"),
        _layer(ticker, LayerId.L8_SA_QUANT, 10.0, source="sa-quant"),
    ]
    for ls in layers:
        _write_layer_event(store, ls, run_ref="bearish-full-seed")
    return layers


def _seed_mixed_full_tier1(store: EventStore, ticker: str) -> list[LayerScore]:
    """Seed a mixed-signal ticker with all 8 layers (review zone)."""
    layers = [
        _layer(ticker, LayerId.L1_PEAD, 62.0, source="pead"),
        _layer(ticker, LayerId.L2_INSIDER, 55.0, source="insider"),
        _layer(ticker, LayerId.L3_SHORT_INTEREST, 48.0, source="finra-si"),
        _layer(ticker, LayerId.L4_ANALYST_REVISIONS, 58.0, source="analyst-rev"),
        _layer(ticker, LayerId.L5_CONGRESSIONAL, 52.0, source="capitol-trades"),
        _layer(ticker, LayerId.L6_NEWS_SENTIMENT, 60.0, source="news-sentiment"),
        _layer(ticker, LayerId.L7_TECHNICAL, 45.0, source="technical"),
        _layer(ticker, LayerId.L8_SA_QUANT, 56.0, source="sa-quant"),
    ]
    for ls in layers:
        _write_layer_event(store, ls, run_ref="mixed-full-seed")
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

        with ASGITestClient(server.app) as client:
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

        with ASGITestClient(server.app) as client:
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

        with ASGITestClient(server.app) as client:
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

        with ASGITestClient(server.app) as client:
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

        with ASGITestClient(server.app) as client:
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


# ═══════════════════════════════════════════════════════════════════════════
# F-008: Phase F Acceptance Harness — Full Tier-1 (L1-L8) Coverage
# ═══════════════════════════════════════════════════════════════════════════


# ── 9. Full Tier-1 (L1-L8) Composite Scoring ──────────────────────────────

class TestFullTier1Composite:
    """F-008: Validate composite scoring with all 8 signal layers present."""

    def test_eight_layer_bullish_auto_execute(self, tmp_path):
        """All 8 layers strongly bullish → composite ≥ 70 → auto_execute_buy."""
        db_path = str(tmp_path / "full_bullish.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        layers = _seed_bullish_full_tier1(store, "AAPL")

        result = score_layer_payloads(ticker="AAPL", as_of=AS_OF, layers=layers)
        assert isinstance(result, CompositeResult)
        assert result.action == DecisionAction.AUTO_EXECUTE_BUY
        assert result.final_score >= 70.0
        assert len(result.layer_scores) == 8

    def test_eight_layer_bearish_short_candidate(self, tmp_path):
        """All 8 layers strongly bearish → composite ≤ 30 → short_candidate."""
        db_path = str(tmp_path / "full_bearish.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        layers = _seed_bearish_full_tier1(store, "TSLA")

        result = score_layer_payloads(ticker="TSLA", as_of=AS_OF, layers=layers)
        assert result.final_score <= 30.0
        assert result.action in (
            DecisionAction.SHORT_CANDIDATE,
            DecisionAction.NO_ACTION,
        )
        assert len(result.layer_scores) == 8

    def test_eight_layer_mixed_flags_review(self, tmp_path):
        """All 8 layers mixed → 50-69 → flag_for_review."""
        db_path = str(tmp_path / "full_mixed.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        layers = _seed_mixed_full_tier1(store, "GOOG")

        result = score_layer_payloads(ticker="GOOG", as_of=AS_OF, layers=layers)
        assert 40.0 <= result.final_score <= 69.0
        assert result.action == DecisionAction.FLAG_FOR_REVIEW
        assert len(result.layer_scores) == 8

    def test_active_layer_weight_sum_equals_one(self):
        """All active composite weights sum to exactly 1.0."""
        layers = [_layer("SPY", layer_id, 75.0) for layer_id in LAYER_ORDER]
        request = CompositeRequest(
            ticker="SPY", as_of=AS_OF, layer_scores=tuple(layers),
        )
        from app.signal.composite import _active_weights
        weights = _active_weights(request)
        assert pytest.approx(sum(weights.values()), abs=1e-6) == 1.0
        assert len(weights) == len(LAYER_ORDER)

    def test_eight_layer_equal_scores_preserves_value(self):
        """All 8 layers at 75.0 → weighted = 75.0 regardless of weights."""
        layers = [_layer("SPY", layer_id, 75.0) for layer_id in LAYER_ORDER]
        result = score_layer_payloads("SPY", AS_OF, layers)
        assert result.weighted_score == pytest.approx(75.0, abs=0.01)

    def test_eight_layer_convergence_bonus_applied(self):
        """8 bullish layers → convergence bonus > 0, final > weighted."""
        layers = [
            _layer("NVDA", LayerId.L1_PEAD, 85.0),
            _layer("NVDA", LayerId.L2_INSIDER, 80.0),
            _layer("NVDA", LayerId.L3_SHORT_INTEREST, 78.0),
            _layer("NVDA", LayerId.L4_ANALYST_REVISIONS, 82.0),
            _layer("NVDA", LayerId.L5_CONGRESSIONAL, 76.0),
            _layer("NVDA", LayerId.L6_NEWS_SENTIMENT, 79.0),
            _layer("NVDA", LayerId.L7_TECHNICAL, 74.0),
            _layer("NVDA", LayerId.L8_SA_QUANT, 88.0),
        ]
        result = score_layer_payloads("NVDA", AS_OF, layers)
        assert result.convergence_bonus_pct > 0.0
        assert result.final_score > result.weighted_score

    def test_eight_layer_per_layer_scores_in_result(self):
        """All 8 layer scores appear in result.layer_scores mapping."""
        layers = [_layer("AMZN", layer_id, 50.0 + i * 5)
                  for i, layer_id in enumerate(LAYER_ORDER)]
        result = score_layer_payloads("AMZN", AS_OF, layers)
        for i, layer_id in enumerate(LAYER_ORDER):
            assert layer_id in result.layer_scores
            assert result.layer_scores[layer_id] == 50.0 + i * 5

    def test_shadow_cycle_full_tier1_scored(self, tmp_path, monkeypatch):
        """Shadow cycle with 8-layer seeded ticker produces scored result."""
        db_path = str(tmp_path / "shadow_full.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _seed_bullish_full_tier1(store, "AAPL")
        _seed_bearish_full_tier1(store, "TSLA")
        _seed_mixed_full_tier1(store, "GOOG")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path, min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )
        assert report["summary"]["tickers_total"] == 3
        assert report["summary"]["tickers_scored"] == 3

        results_by = {r["ticker"]: r for r in report["results"]}
        assert results_by["AAPL"]["layer_count"] == 8
        assert results_by["TSLA"]["layer_count"] == 8
        assert results_by["GOOG"]["layer_count"] == 8
        assert results_by["AAPL"]["action"] == "auto_execute_buy"
        assert results_by["TSLA"]["action"] in ("short_candidate", "no_action")
        assert results_by["GOOG"]["action"] == "flag_for_review"


# ── 10. Data Quality Penalties (F-006) ────────────────────────────────────

class TestDataQualityPenalties:
    """F-008: Data quality penalty pipeline from F-006 composite v3."""

    def test_warning_layer_applies_small_penalty(self):
        """Layers with _freshness_state=warning reduce final score."""
        layers = [
            _layer("AAPL", LayerId.L1_PEAD, 80.0,
                   details={"e2e": True, "_freshness_state": "warning"}),
            _layer("AAPL", LayerId.L8_SA_QUANT, 80.0),
        ]
        cfg = CompositeScoringConfig(warning_layer_penalty_pct=5.0)
        result_clean = score_layer_payloads("AAPL", AS_OF, [
            _layer("AAPL", LayerId.L1_PEAD, 80.0),
            _layer("AAPL", LayerId.L8_SA_QUANT, 80.0),
        ])
        result_warn = score_layer_payloads(
            "AAPL", AS_OF, layers, scoring_config=cfg,
        )
        # Warning penalty should reduce score
        assert result_warn.final_score < result_clean.final_score
        assert any("quality_penalty_pct=" in n for n in result_warn.notes)

    def test_stale_layer_applies_large_penalty(self):
        """Layers with _freshness_state=stale reduce final score significantly."""
        layers = [
            _layer("AAPL", LayerId.L1_PEAD, 80.0,
                   details={"e2e": True, "_freshness_state": "stale"}),
            _layer("AAPL", LayerId.L8_SA_QUANT, 80.0),
        ]
        cfg = CompositeScoringConfig(stale_layer_penalty_pct=15.0)
        result = score_layer_payloads(
            "AAPL", AS_OF, layers, scoring_config=cfg,
        )
        # Should have stale_layers note
        assert any("stale_layers=" in n for n in result.notes)
        # Score reduced by ~15%: 80 * 0.85 = 68
        assert result.final_score < 80.0

    def test_quality_penalty_capped_at_max(self):
        """Even many stale layers cap penalty at max_data_quality_penalty_pct."""
        layers = [
            _layer("AAPL", layer_id, 80.0,
                   details={"e2e": True, "_freshness_state": "stale"})
            for layer_id in LAYER_ORDER
        ]
        cfg = CompositeScoringConfig(
            stale_layer_penalty_pct=15.0,
            max_data_quality_penalty_pct=40.0,
        )
        result = score_layer_payloads(
            "AAPL", AS_OF, layers, scoring_config=cfg,
        )
        # 8 stale * 15% = 120%, but capped at 40%
        # So final ≈ 80 * 0.60 = 48 (with convergence)
        assert result.final_score >= 80.0 * 0.55  # Allow convergence margin
        assert any("quality_penalty_pct=" in n for n in result.notes)

    def test_fresh_layers_no_penalty(self):
        """All layers with _freshness_state=fresh → zero quality penalty."""
        layers = [
            _layer("AAPL", LayerId.L1_PEAD, 80.0,
                   details={"e2e": True, "_freshness_state": "fresh"}),
            _layer("AAPL", LayerId.L8_SA_QUANT, 80.0,
                   details={"e2e": True, "_freshness_state": "fresh"}),
        ]
        result = score_layer_payloads("AAPL", AS_OF, layers)
        # No penalty → weighted = final (up to convergence)
        penalty_note = [n for n in result.notes if "quality_penalty_pct=" in n]
        assert penalty_note
        assert "quality_penalty_pct=0" in penalty_note[0]

    def test_missing_required_layer_noted_in_result(self):
        """Missing required layers appear in notes even without veto."""
        layers = [
            _layer("AAPL", LayerId.L1_PEAD, 80.0),
            _layer("AAPL", LayerId.L8_SA_QUANT, 80.0),
        ]
        cfg = CompositeScoringConfig(
            required_layers=(LayerId.L1_PEAD, LayerId.L2_INSIDER, LayerId.L8_SA_QUANT),
            emit_missing_required_veto=False,
        )
        result = score_layer_payloads(
            "AAPL", AS_OF, layers, scoring_config=cfg,
        )
        assert any("missing_required_layers=" in n for n in result.notes)
        assert any("l2_insider" in n for n in result.notes)


# ── 11. Shadow Cycle Freshness Enforcement (F-006) ───────────────────────

class TestShadowFreshnessEnforcement:
    """F-008: Shadow cycle freshness annotation and blocking from F-006."""

    def test_shadow_cycle_annotates_freshness_states(self, tmp_path, monkeypatch):
        """Shadow cycle clones carry _freshness_state in result freshness."""
        db_path = str(tmp_path / "shadow_freshness.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        # Fresh L1 (scored just now) and old L6 (scored 4 days ago — stale for L6)
        fresh = _layer("AAPL", LayerId.L1_PEAD, 80.0, source="pead")
        _write_layer_event(store, fresh, retrieved_at=RETRIEVED_AT, run_ref="l1")

        old_news = LayerScore(
            layer_id=LayerId.L6_NEWS_SENTIMENT, ticker="AAPL", score=70.0,
            as_of="2026-02-26T12:00:00Z", source="news", provenance_ref="e2e:old",
            confidence=0.85, details={"e2e": True},
        )
        _write_layer_event(store, old_news, retrieved_at="2026-02-26T12:01:00Z",
                           run_ref="l6-old")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path, min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )
        aapl = report["results"][0]
        # L6 news has max_age=24h, was scored 4 days ago → stale
        assert "l6_news_sentiment" in aapl["freshness"]["stale_layers"]
        assert aapl["freshness"]["layer_states"]["l1_pead"] == "fresh"
        assert aapl["freshness"]["layer_states"]["l6_news_sentiment"] == "stale"

    def test_shadow_cycle_scores_with_warning_status(self, tmp_path, monkeypatch):
        """Warning layers penalize but don't block → scored_warning_layers."""
        db_path = str(tmp_path / "shadow_warning.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        # L1 PEAD: warn_age=36h, max_age=72h; scored ~40h ago → warning
        # AS_OF is 2026-03-02T12:00:00Z, so 40h earlier = 2026-02-28T20:00:00Z
        warning_pead = LayerScore(
            layer_id=LayerId.L1_PEAD, ticker="AAPL", score=80.0,
            as_of="2026-02-28T20:00:00Z", source="pead",
            provenance_ref="e2e:warn", confidence=0.85, details={"e2e": True},
        )
        _write_layer_event(store, warning_pead,
                           retrieved_at="2026-02-28T20:01:00Z", run_ref="l1-warn")

        fresh_l8 = _layer("AAPL", LayerId.L8_SA_QUANT, 78.0, source="sa-quant")
        _write_layer_event(store, fresh_l8, retrieved_at=RETRIEVED_AT, run_ref="l8")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path, min_layers_for_score=2,
            now_fn=lambda: AS_OF,
        )
        aapl = report["results"][0]
        assert aapl["status"] == "scored_warning_layers"
        assert "l1_pead" in aapl["freshness"]["warning_layers"]
        # Should still produce a score (not blocked)
        assert aapl["final_score"] > 0

    def test_shadow_cycle_enforce_missing_required_blocks(
        self, tmp_path, monkeypatch,
    ):
        """enforce_required_layers=True blocks when required layers missing."""
        db_path = str(tmp_path / "shadow_enforce.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _layer_l1 = _layer("AAPL", LayerId.L1_PEAD, 85.0, source="pead")
        _layer_l8 = _layer("AAPL", LayerId.L8_SA_QUANT, 82.0, source="sa-quant")
        _write_layer_event(store, _layer_l1, retrieved_at=RETRIEVED_AT, run_ref="l1")
        _write_layer_event(store, _layer_l8, retrieved_at=RETRIEVED_AT, run_ref="l8")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            required_layers=LAYER_ORDER,
            min_layers_for_score=2,
            enforce_required_layers=True,
            now_fn=lambda: AS_OF,
        )
        aapl = report["results"][0]
        assert aapl["status"] == "blocked_missing_required_layers"
        assert aapl["action"] == "no_action"
        assert "missing_required_layers" in aapl["vetoes"]

    def test_shadow_cycle_soft_mode_scores_partial_layers(
        self, tmp_path, monkeypatch,
    ):
        """enforce_required_layers=False (default) scores even with missing layers."""
        db_path = str(tmp_path / "shadow_soft.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        for ls in [
            _layer("AAPL", LayerId.L1_PEAD, 85.0, source="pead"),
            _layer("AAPL", LayerId.L2_INSIDER, 82.0, source="insider"),
            _layer("AAPL", LayerId.L8_SA_QUANT, 88.0, source="sa-quant"),
        ]:
            _write_layer_event(store, ls, retrieved_at=RETRIEVED_AT, run_ref="soft")

        report = signal_shadow.run_signal_shadow_cycle(
            db_path=db_path,
            required_layers=LAYER_ORDER,
            min_layers_for_score=2,
            enforce_required_layers=False,
            now_fn=lambda: AS_OF,
        )
        aapl = report["results"][0]
        # Scored but with missing-required noted
        assert aapl["status"].startswith("scored")
        assert aapl["final_score"] > 0
        assert len(aapl["missing_required_layers"]) == len(LAYER_ORDER) - 3


# ── 12. Tier-1 Ranked Candidates (F-007) ──────────────────────────────────

class TestTier1RankedCandidates:
    """F-008: Ranked candidate derivation from F-007."""

    def test_ranked_candidates_exclude_no_action(self):
        """no_action rows are excluded from ranking."""
        report = {
            "results": [
                {"ticker": "AAPL", "status": "scored", "action": "auto_execute_buy",
                 "final_score": 82.0, "weighted_score": 80.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
                {"ticker": "GOOG", "status": "scored", "action": "no_action",
                 "final_score": 45.0, "weighted_score": 44.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
            ],
        }
        ranked = build_ranked_candidates(report, limit=10)
        assert len(ranked) == 1
        assert ranked[0]["ticker"] == "AAPL"

    def test_ranked_candidates_sort_by_rank_score_desc(self):
        """Candidates sorted by rank_score descending (highest first)."""
        report = {
            "results": [
                {"ticker": "MSFT", "status": "scored", "action": "auto_execute_buy",
                 "final_score": 72.0, "weighted_score": 70.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
                {"ticker": "AAPL", "status": "scored", "action": "auto_execute_buy",
                 "final_score": 88.0, "weighted_score": 85.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
                {"ticker": "GOOG", "status": "scored", "action": "flag_for_review",
                 "final_score": 55.0, "weighted_score": 53.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
            ],
        }
        ranked = build_ranked_candidates(report, limit=10)
        assert [r["ticker"] for r in ranked] == ["AAPL", "MSFT", "GOOG"]
        assert ranked[0]["rank"] == 1
        assert ranked[1]["rank"] == 2
        assert ranked[2]["rank"] == 3

    def test_short_candidate_rank_score_inverted(self):
        """Short candidates use 100 - final_score for ranking sort."""
        report = {
            "results": [
                {"ticker": "AAPL", "status": "scored", "action": "auto_execute_buy",
                 "final_score": 82.0, "weighted_score": 80.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
                {"ticker": "TSLA", "status": "scored", "action": "short_candidate",
                 "final_score": 12.0, "weighted_score": 14.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
            ],
        }
        ranked = build_ranked_candidates(report, limit=10)
        tsla = next(r for r in ranked if r["ticker"] == "TSLA")
        assert tsla["rank_score"] == pytest.approx(88.0)  # 100 - 12

    def test_ranked_candidates_respect_limit(self):
        """Ranking is capped at the limit parameter."""
        report = {
            "results": [
                {"ticker": f"T{i}", "status": "scored", "action": "auto_execute_buy",
                 "final_score": 90.0 - i, "weighted_score": 88.0 - i, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []}
                for i in range(10)
            ],
        }
        ranked = build_ranked_candidates(report, limit=3)
        assert len(ranked) == 3
        assert ranked[0]["ticker"] == "T0"
        assert ranked[2]["ticker"] == "T2"

    def test_ranked_candidates_include_quality_metadata(self):
        """Each ranked row includes quality penalty and warning/stale counts."""
        report = {
            "results": [
                {"ticker": "AAPL", "status": "scored_warning_layers",
                 "action": "auto_execute_buy",
                 "final_score": 78.0, "weighted_score": 80.0, "layer_count": 8,
                 "missing_required_layers": ["l3_short_interest"],
                 "freshness": {"warning_layers": ["l2_insider"], "stale_layers": []},
                 "notes": ["quality_penalty_pct=2.5"]},
            ],
        }
        ranked = build_ranked_candidates(report, limit=10)
        assert len(ranked) == 1
        row = ranked[0]
        assert row["quality_penalty_pct"] == pytest.approx(2.5)
        assert row["warning_layers"] == ["l2_insider"]
        assert row["missing_required_layers"] == ["l3_short_interest"]

    def test_ranked_candidates_empty_results_safe(self):
        """Empty or missing results field returns empty list."""
        assert build_ranked_candidates({}, limit=10) == []
        assert build_ranked_candidates({"results": []}, limit=10) == []

    def test_insufficient_layer_rows_excluded(self):
        """Rows with status=insufficient_layers are not ranked."""
        report = {
            "results": [
                {"ticker": "AAPL", "status": "scored", "action": "auto_execute_buy",
                 "final_score": 82.0, "weighted_score": 80.0, "layer_count": 8,
                 "missing_required_layers": [],
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "notes": []},
                {"ticker": "AMD", "status": "insufficient_layers",
                 "layer_count": 1, "missing_required_layers": ["l2_insider"]},
            ],
        }
        ranked = build_ranked_candidates(report, limit=10)
        assert len(ranked) == 1
        assert ranked[0]["ticker"] == "AAPL"


# ── 13. Freshness Diagnostics (F-007) ─────────────────────────────────────

class TestFreshnessDiagnostics:
    """F-008: Freshness diagnostic aggregation from F-007."""

    def test_diagnostics_count_warnings_and_stale(self):
        """Diagnostics correctly count warning and stale tickers/layers."""
        report = {
            "results": [
                {"ticker": "AAPL", "status": "scored_warning_layers",
                 "freshness": {"warning_layers": ["l1_pead", "l2_insider"],
                               "stale_layers": []},
                 "missing_required_layers": []},
                {"ticker": "TSLA", "status": "blocked_stale_layers",
                 "freshness": {"warning_layers": [],
                               "stale_layers": ["l6_news_sentiment"]},
                 "missing_required_layers": []},
                {"ticker": "GOOG", "status": "scored",
                 "freshness": {"warning_layers": ["l7_technical"],
                               "stale_layers": []},
                 "missing_required_layers": ["l3_short_interest"]},
            ],
        }
        diag = summarize_freshness_diagnostics(report)
        assert diag["tickers_with_warnings"] == 2  # AAPL, GOOG
        assert diag["tickers_with_stale"] == 1  # TSLA
        assert diag["total_warning_layers"] == 3  # l1, l2, l7
        assert diag["total_stale_layers"] == 1  # l6
        assert diag["blocked_stale_layers"] == 1
        assert diag["scored_missing_required_layers"] == 1  # GOOG

    def test_diagnostics_empty_report(self):
        """Empty report returns zero counts for all diagnostics."""
        diag = summarize_freshness_diagnostics({"results": []})
        assert diag["tickers_with_warnings"] == 0
        assert diag["tickers_with_stale"] == 0
        assert diag["total_warning_layers"] == 0
        assert diag["total_stale_layers"] == 0

    def test_diagnostics_blocked_missing_required(self):
        """Blocked-missing-required tickers counted separately."""
        report = {
            "results": [
                {"ticker": "AAPL", "status": "blocked_missing_required_layers",
                 "freshness": {"warning_layers": [], "stale_layers": []},
                 "missing_required_layers": ["l2_insider", "l4_analyst_revisions"]},
            ],
        }
        diag = summarize_freshness_diagnostics(report)
        assert diag["blocked_missing_required_layers"] == 1

    def test_diagnostics_no_results_key(self):
        """Missing results key returns zero counts (defensive)."""
        diag = summarize_freshness_diagnostics({})
        assert diag["tickers_with_warnings"] == 0


# ── 14. Enriched API Payload (F-007) ──────────────────────────────────────

class TestEnrichedPayload:
    """F-008: enrich_signal_shadow_payload adds ranking + diagnostics."""

    def test_enrich_adds_ranked_candidates(self):
        """Enriched payload includes ranked_candidates under report."""
        payload = {
            "ok": True, "state": "ready", "has_report": True,
            "report": {
                "run_id": "test",
                "results": [
                    {"ticker": "AAPL", "status": "scored",
                     "action": "auto_execute_buy",
                     "final_score": 82.0, "weighted_score": 80.0,
                     "layer_count": 8, "missing_required_layers": [],
                     "vetoes": ["research_blocking_objections"],
                     "layer_scores": {"l9_research": 35.0},
                     "freshness": {"warning_layers": [], "stale_layers": []},
                     "notes": []},
                ],
            },
        }
        enriched = enrich_signal_shadow_payload(payload)
        assert "ranked_candidates" in enriched["report"]
        assert enriched["report"]["ranked_candidates"][0]["ticker"] == "AAPL"
        assert enriched["report"]["ranked_candidates"][0]["research_layer_score"] == 35.0
        assert enriched["report"]["ranked_candidates"][0]["research_vetoes"] == ["research_blocking_objections"]
        assert enriched["report"]["results"][0]["blocked_by_research"] is True
        assert "freshness_diagnostics" in enriched
        assert enriched["research_overlay_diagnostics"]["tickers_blocked_by_research"] == 1

    def test_enrich_idle_payload_unchanged(self):
        """Idle/no-report payload passes through without modification."""
        payload = {
            "ok": True, "state": "idle", "has_report": False,
            "report": None, "event_stats": None,
        }
        enriched = enrich_signal_shadow_payload(payload)
        assert enriched["has_report"] is False
        assert "ranked_candidates" not in (enriched.get("report") or {})

    def test_enrich_respects_ranking_limit(self):
        """Enriched ranking respects the limit parameter."""
        results = [
            {"ticker": f"T{i}", "status": "scored", "action": "auto_execute_buy",
             "final_score": 90.0 - i, "weighted_score": 88.0 - i,
             "layer_count": 8, "missing_required_layers": [],
             "freshness": {"warning_layers": [], "stale_layers": []},
             "notes": []}
            for i in range(10)
        ]
        payload = {
            "ok": True, "state": "ready", "has_report": True,
            "report": {"run_id": "test", "results": results},
        }
        enriched = enrich_signal_shadow_payload(payload, ranking_limit=3)
        assert len(enriched["report"]["ranked_candidates"]) == 3


# ── 15. Tier-1 Job Orchestration (F-007) ──────────────────────────────────

class TestTier1JobOrchestration:
    """F-008: run_tier1_shadow_jobs end-to-end orchestration."""

    def test_tier1_jobs_produces_ranked_output(self, tmp_path, monkeypatch):
        """Tier-1 jobs produce ranked candidates from seeded layers."""
        db_path = str(tmp_path / "tier1_ranked.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _seed_bullish_full_tier1(store, "AAPL")
        _seed_bearish_full_tier1(store, "TSLA")

        class FakeSAQuant:
            def __init__(self, **kw): pass
            def run(self, **kw):
                return {"job_id": "fake", "tickers_total": 0,
                        "tickers_success": 0, "tickers_failed": 0}

        from intelligence.jobs import signal_layer_jobs
        result = signal_layer_jobs.run_tier1_shadow_jobs(
            db_path=db_path,
            tickers=["AAPL", "TSLA"],
            as_of=AS_OF,
            config_obj=Tier1ShadowJobsConfig(
                min_layers_for_score=2,
                enforce_required_layers=False,
            ),
            sa_quant_runner=FakeSAQuant(),
            shadow_runner=lambda **kw: signal_shadow.run_signal_shadow_cycle(**kw),
            now_fn=lambda: AS_OF,
        )
        assert "ranked_candidates" in result
        assert "freshness_diagnostics" in result
        assert "shadow_report" in result
        # At least one ranked candidate (AAPL bullish = auto_execute_buy)
        tickers = [r["ticker"] for r in result["ranked_candidates"]]
        assert "AAPL" in tickers

    def test_tier1_jobs_reports_layer_job_statuses(self, tmp_path, monkeypatch):
        """Each layer job shows status (completed/skipped/failed)."""
        db_path = str(tmp_path / "tier1_statuses.db")
        trade_db.init_db(db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        class FakeSAQuant:
            def __init__(self, **kw): pass
            def run(self, **kw):
                return {"job_id": "fake", "tickers_total": 2,
                        "tickers_success": 2, "tickers_failed": 0}

        from intelligence.jobs import signal_layer_jobs
        result = signal_layer_jobs.run_tier1_shadow_jobs(
            db_path=db_path,
            tickers=["AAPL"],
            as_of=AS_OF,
            config_obj=Tier1ShadowJobsConfig(min_layers_for_score=2),
            sa_quant_runner=FakeSAQuant(),
            shadow_runner=lambda **kw: signal_shadow.run_signal_shadow_cycle(**kw),
            now_fn=lambda: AS_OF,
        )
        layer_jobs = result["layer_jobs"]
        # SA Quant should be completed; other layers may be completed or failed
        # depending on whether their data sources are available
        assert layer_jobs["l8_sa_quant"]["status"] == "completed"
        assert layer_jobs["l1_pead"]["status"] in ("completed", "failed")
        assert layer_jobs["l3_short_interest"]["status"] == "skipped"

    def test_tier1_jobs_sa_quant_failure_handled(self, tmp_path, monkeypatch):
        """SA Quant failure is captured, shadow cycle still runs."""
        db_path = str(tmp_path / "tier1_sa_fail.db")
        trade_db.init_db(db_path)
        store = EventStore(db_path=db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        _seed_bullish_full_tier1(store, "AAPL")

        class FailingSAQuant:
            def __init__(self, **kw): pass
            def run(self, **kw):
                raise RuntimeError("API key expired")

        from intelligence.jobs import signal_layer_jobs
        result = signal_layer_jobs.run_tier1_shadow_jobs(
            db_path=db_path,
            tickers=["AAPL"],
            as_of=AS_OF,
            config_obj=Tier1ShadowJobsConfig(
                min_layers_for_score=2,
                enforce_required_layers=False,
            ),
            sa_quant_runner=FailingSAQuant(),
            shadow_runner=lambda **kw: signal_shadow.run_signal_shadow_cycle(**kw),
            now_fn=lambda: AS_OF,
        )
        # SA Quant failed but shadow cycle still produced results
        assert result["layer_jobs"]["l8_sa_quant"]["status"] == "failed"
        assert "API key expired" in result["layer_jobs"]["l8_sa_quant"]["detail"]
        assert result["shadow_report"]["summary"]["tickers_total"] >= 1

    def test_tier1_result_json_serializable(self, tmp_path, monkeypatch):
        """result_json field is valid JSON with summary data."""
        db_path = str(tmp_path / "tier1_json.db")
        trade_db.init_db(db_path)
        monkeypatch.setattr(signal_shadow.config, "STRATEGY_SLOTS", [])

        class FakeSAQuant:
            def __init__(self, **kw): pass
            def run(self, **kw):
                return {"job_id": "j1", "tickers_total": 0,
                        "tickers_success": 0, "tickers_failed": 0}

        from intelligence.jobs import signal_layer_jobs
        result = signal_layer_jobs.run_tier1_shadow_jobs(
            db_path=db_path, tickers=[], as_of=AS_OF,
            config_obj=Tier1ShadowJobsConfig(min_layers_for_score=2),
            sa_quant_runner=FakeSAQuant(),
            shadow_runner=lambda **kw: signal_shadow.run_signal_shadow_cycle(**kw),
            now_fn=lambda: AS_OF,
        )
        parsed = json.loads(result["result_json"])
        assert "run_id" in parsed
        assert "ranked_candidates" in parsed
        assert "freshness_diagnostics" in parsed
