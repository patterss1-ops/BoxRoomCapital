"""Tests for F-005: L7 Technical Overlay scorer."""

from __future__ import annotations

import pytest

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from app.signal.layers.technical_overlay import (
    DEFAULT_CONFIG,
    TechnicalScoringConfig,
    TechnicalSnapshot,
    score_technical,
    score_technical_batch,
)


AS_OF = "2026-03-01T00:00:00Z"


def _snap(
    ticker: str = "AAPL",
    snapshot_date: str = "2026-02-28",
    close: float = 185.0,
    sma_50: float = 180.0,
    sma_200: float = 170.0,
    rsi_14: float = 62.0,
    volume: float = 50_000_000.0,
    avg_volume_20d: float = 45_000_000.0,
    ema_20: float = 183.0,
) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        ticker=ticker,
        snapshot_date=snapshot_date,
        close=close,
        sma_50=sma_50,
        sma_200=sma_200,
        rsi_14=rsi_14,
        volume=volume,
        avg_volume_20d=avg_volume_20d,
        ema_20=ema_20,
    )


# ── LayerScore contract compliance ───────────────────────────────────

class TestLayerScoreContract:
    def test_returns_layer_score_with_correct_layer_id(self):
        score = score_technical("AAPL", [_snap()], AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L7_TECHNICAL

    def test_ticker_is_uppercased(self):
        score = score_technical("aapl", [_snap(ticker="aapl")], AS_OF)
        assert score.ticker == "AAPL"

    def test_score_in_valid_range(self):
        score = score_technical("AAPL", [_snap()], AS_OF)
        assert 0.0 <= score.score <= 100.0

    def test_confidence_in_valid_range(self):
        score = score_technical("AAPL", [_snap()], AS_OF)
        assert score.confidence is not None
        assert 0.0 <= score.confidence <= 1.0

    def test_round_trip_dict_serialization(self):
        score = score_technical("AAPL", [_snap()], AS_OF)
        d = score.to_dict()
        restored = LayerScore.from_dict(d)
        assert restored.layer_id == score.layer_id
        assert restored.score == score.score

    def test_source_matches_config(self):
        score = score_technical("AAPL", [_snap()], AS_OF)
        assert score.source == DEFAULT_CONFIG.source

    def test_required_detail_keys_present(self):
        """All required keys from the layer registry contract."""
        score = score_technical("AAPL", [_snap()], AS_OF)
        for key in ("rsi14", "above_50dma", "above_200dma", "volume_ratio"):
            assert key in score.details, f"Missing required detail key: {key}"


# ── No-data / edge cases ─────────────────────────────────────────────

class TestNoData:
    def test_no_snapshots_returns_zero_score(self):
        score = score_technical("AAPL", [], AS_OF)
        assert score.score == 0.0
        assert score.confidence == 0.0
        assert score.details["reason"] == "no_eligible_snapshots"

    def test_future_snapshots_filtered(self):
        future = _snap(snapshot_date="2026-04-01")
        score = score_technical("AAPL", [future], AS_OF)
        assert score.score == 0.0

    def test_minimal_data_snapshot(self):
        """Snapshot with only close price (no indicators)."""
        snap = TechnicalSnapshot(
            ticker="AAPL",
            snapshot_date="2026-02-28",
            close=185.0,
        )
        score = score_technical("AAPL", [snap], AS_OF)
        assert 0.0 <= score.score <= 100.0
        assert score.confidence == 0.0  # no indicator data


# ── Trend scoring ────────────────────────────────────────────────────

class TestTrendScoring:
    def test_above_both_mas_max_trend(self):
        snap = _snap(close=190.0, sma_50=180.0, sma_200=170.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 30.0

    def test_above_50_below_200(self):
        snap = _snap(close=175.0, sma_50=170.0, sma_200=180.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 20.0

    def test_below_50_above_200(self):
        """Pullback scenario — below 50-DMA but above 200-DMA."""
        snap = _snap(close=175.0, sma_50=180.0, sma_200=170.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 15.0

    def test_below_both_mas_bearish(self):
        snap = _snap(close=160.0, sma_50=170.0, sma_200=180.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 5.0

    def test_no_ma_data(self):
        snap = TechnicalSnapshot(ticker="AAPL", snapshot_date="2026-02-28",
                                 close=185.0, rsi_14=55.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == DEFAULT_CONFIG.trend_no_data_score


# ── Momentum scoring ─────────────────────────────────────────────────

class TestMomentumScoring:
    def test_strong_bullish_momentum(self):
        snap = _snap(rsi_14=65.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["momentum"] == 30.0

    def test_overbought_slightly_penalised(self):
        snap = _snap(rsi_14=75.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["momentum"] == 15.0

    def test_neutral_momentum(self):
        snap = _snap(rsi_14=45.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["momentum"] == 15.0

    def test_oversold_low_momentum(self):
        snap = _snap(rsi_14=25.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["momentum"] == 3.0

    def test_no_rsi_data(self):
        snap = TechnicalSnapshot(ticker="AAPL", snapshot_date="2026-02-28",
                                 close=185.0, sma_50=180.0, sma_200=170.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["momentum"] == DEFAULT_CONFIG.rsi_no_data_score


# ── Volume scoring ───────────────────────────────────────────────────

class TestVolumeScoring:
    def test_high_volume_conviction(self):
        snap = _snap(volume=150_000_000, avg_volume_20d=45_000_000)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["volume"] == 20.0

    def test_above_average_volume(self):
        snap = _snap(volume=70_000_000, avg_volume_20d=45_000_000)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["volume"] == 12.0

    def test_average_volume(self):
        snap = _snap(volume=45_000_000, avg_volume_20d=45_000_000)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["volume"] == 8.0

    def test_low_volume(self):
        # 15M / 45M = 0.33 ratio → below 0.5 bucket → 2.0 points
        snap = _snap(volume=15_000_000, avg_volume_20d=45_000_000)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["volume"] == 2.0

    def test_no_volume_data(self):
        snap = TechnicalSnapshot(ticker="AAPL", snapshot_date="2026-02-28",
                                 close=185.0, rsi_14=55.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["volume"] == DEFAULT_CONFIG.volume_no_data_score


# ── Pattern scoring ──────────────────────────────────────────────────

class TestPatternScoring:
    def test_golden_cross(self):
        snap = _snap(sma_50=180.0, sma_200=170.0)  # 50 > 200
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["pattern"] == DEFAULT_CONFIG.pattern_golden_cross_score

    def test_death_cross(self):
        snap = _snap(sma_50=165.0, sma_200=170.0)  # 50 < 200
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["pattern"] == DEFAULT_CONFIG.pattern_death_cross_score

    def test_no_cross_data(self):
        snap = TechnicalSnapshot(ticker="AAPL", snapshot_date="2026-02-28",
                                 close=185.0, rsi_14=55.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.details["sub_scores"]["pattern"] == DEFAULT_CONFIG.pattern_no_cross_data_score


# ── Composite scenarios ──────────────────────────────────────────────

class TestCompositeScenarios:
    def test_full_bullish_setup_high_score(self):
        """All indicators bullish → high score."""
        snap = _snap(
            close=190.0, sma_50=180.0, sma_200=170.0,
            rsi_14=65.0,
            volume=100_000_000, avg_volume_20d=45_000_000,
        )
        score = score_technical("AAPL", [snap], AS_OF)
        # Trend=30 + Momentum=30 + Volume=16 + Pattern=20 = 96
        assert score.score >= 85.0

    def test_full_bearish_setup_low_score(self):
        """All indicators bearish → low score."""
        snap = _snap(
            close=155.0, sma_50=170.0, sma_200=180.0,  # below both, death cross
            rsi_14=25.0,                                  # oversold
            volume=15_000_000, avg_volume_20d=45_000_000, # low volume
        )
        score = score_technical("AAPL", [snap], AS_OF)
        # Trend=5 + Momentum=3 + Volume=4 + Pattern=3 = 15
        assert score.score <= 20.0

    def test_mixed_signals_mid_score(self):
        """Some bullish, some bearish indicators → mid score."""
        snap = _snap(
            close=175.0, sma_50=180.0, sma_200=170.0,  # pullback
            rsi_14=52.0,                                  # neutral RSI
            volume=45_000_000, avg_volume_20d=45_000_000, # average volume
        )
        score = score_technical("AAPL", [snap], AS_OF)
        assert 30.0 <= score.score <= 70.0


# ── Confidence ───────────────────────────────────────────────────────

class TestConfidence:
    def test_full_data_full_confidence(self):
        snap = _snap()  # all indicators present
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.confidence == 1.0

    def test_half_data_half_confidence(self):
        snap = TechnicalSnapshot(
            ticker="AAPL", snapshot_date="2026-02-28",
            close=185.0, sma_50=180.0, rsi_14=55.0,
        )
        score = score_technical("AAPL", [snap], AS_OF)
        # sma_50 + rsi_14 available (2 of 4)
        assert score.confidence == 0.5

    def test_no_indicators_zero_confidence(self):
        snap = TechnicalSnapshot(
            ticker="AAPL", snapshot_date="2026-02-28", close=185.0,
        )
        score = score_technical("AAPL", [snap], AS_OF)
        assert score.confidence == 0.0


# ── TechnicalSnapshot properties ─────────────────────────────────────

class TestSnapshotProperties:
    def test_above_50dma(self):
        snap = _snap(close=185.0, sma_50=180.0)
        assert snap.above_50dma is True

    def test_below_50dma(self):
        snap = _snap(close=175.0, sma_50=180.0)
        assert snap.above_50dma is False

    def test_above_50dma_none_when_no_data(self):
        snap = TechnicalSnapshot(ticker="AAPL", snapshot_date="2026-02-28", close=185.0)
        assert snap.above_50dma is None

    def test_volume_ratio(self):
        snap = _snap(volume=90_000_000, avg_volume_20d=45_000_000)
        assert snap.volume_ratio == pytest.approx(2.0)

    def test_volume_ratio_none_when_no_data(self):
        snap = TechnicalSnapshot(ticker="AAPL", snapshot_date="2026-02-28", close=185.0)
        assert snap.volume_ratio is None

    def test_golden_cross_true(self):
        snap = _snap(sma_50=180.0, sma_200=170.0)
        assert snap.golden_cross is True

    def test_golden_cross_false(self):
        snap = _snap(sma_50=165.0, sma_200=170.0)
        assert snap.golden_cross is False


# ── Batch scoring ────────────────────────────────────────────────────

class TestBatchScoring:
    def test_batch_returns_dict(self):
        result = score_technical_batch(
            {
                "AAPL": [_snap(ticker="AAPL")],
                "MSFT": [_snap(ticker="MSFT")],
            },
            AS_OF,
        )
        assert len(result) == 2
        assert all(isinstance(v, LayerScore) for v in result.values())

    def test_batch_empty(self):
        result = score_technical_batch({}, AS_OF)
        assert result == {}


# ── Detail keys alignment with layer registry ────────────────────────

class TestDetailKeysAlignment:
    def test_all_registry_required_keys_present(self):
        from app.signal.layer_registry import get_layer_contract
        contract = get_layer_contract(LayerId.L7_TECHNICAL)
        score = score_technical("AAPL", [_snap()], AS_OF)
        missing = score.missing_detail_keys(contract.required_detail_keys)
        assert missing == (), f"Missing required detail keys: {missing}"

    def test_freshness_evaluates_correctly(self):
        from app.signal.layer_registry import evaluate_freshness, FreshnessState
        score = score_technical("AAPL", [_snap()], AS_OF)
        freshness = evaluate_freshness(score, AS_OF)
        assert freshness == FreshnessState.FRESH


# ── Provenance ───────────────────────────────────────────────────────

class TestProvenance:
    def test_provenance_ref_contains_ticker(self):
        score = score_technical("AAPL", [_snap()], AS_OF)
        assert "AAPL" in score.provenance_ref

    def test_no_data_provenance(self):
        score = score_technical("AAPL", [], AS_OF)
        assert "no-data" in score.provenance_ref

    def test_provenance_includes_rsi(self):
        snap = _snap(rsi_14=62.0)
        score = score_technical("AAPL", [snap], AS_OF)
        assert "rsi62" in score.provenance_ref

    def test_provenance_includes_cross_info(self):
        snap = _snap(sma_50=180.0, sma_200=170.0)  # golden cross
        score = score_technical("AAPL", [snap], AS_OF)
        assert "gc" in score.provenance_ref
