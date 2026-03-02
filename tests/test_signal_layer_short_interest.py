"""Tests for F-002: L3 Short Interest Dynamics scorer."""

from __future__ import annotations

import pytest

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from app.signal.layers.short_interest import (
    DEFAULT_CONFIG,
    ShortInterestScoringConfig,
    score_short_interest,
    score_short_interest_batch,
)
from intelligence.finra_short_interest import (
    ShortInterestSnapshot,
    normalize_snapshots,
)


AS_OF = "2026-03-01T00:00:00Z"


def _snap(
    ticker: str = "GME",
    settlement_date: str = "2026-02-28",
    short_interest: int = 10_000_000,
    avg_daily_volume: float = 5_000_000.0,
    shares_outstanding: int = 100_000_000,
    prior_short_interest: int | None = 12_000_000,
    source_ref: str = "test",
) -> ShortInterestSnapshot:
    return ShortInterestSnapshot(
        ticker=ticker,
        settlement_date=settlement_date,
        short_interest=short_interest,
        avg_daily_volume=avg_daily_volume,
        shares_outstanding=shares_outstanding,
        prior_short_interest=prior_short_interest,
        source_ref=source_ref,
    )


# ── LayerScore contract compliance ───────────────────────────────────

class TestLayerScoreContract:
    def test_returns_layer_score_with_correct_layer_id(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L3_SHORT_INTEREST

    def test_ticker_is_uppercased(self):
        score = score_short_interest("gme", [_snap(ticker="gme")], AS_OF)
        assert score.ticker == "GME"

    def test_score_in_valid_range(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        assert 0.0 <= score.score <= 100.0

    def test_confidence_in_valid_range(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        assert score.confidence is not None
        assert 0.0 <= score.confidence <= 1.0

    def test_round_trip_dict_serialization(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        d = score.to_dict()
        restored = LayerScore.from_dict(d)
        assert restored.layer_id == score.layer_id
        assert restored.score == score.score

    def test_source_matches_config(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        assert score.source == DEFAULT_CONFIG.source

    def test_required_detail_keys_present(self):
        """All required keys from the layer registry contract are present."""
        score = score_short_interest("GME", [_snap()], AS_OF)
        for key in ("short_interest_pct", "short_interest_change_pct",
                     "days_to_cover", "window_end"):
            assert key in score.details, f"Missing required detail key: {key}"


# ── No-data / edge cases ─────────────────────────────────────────────

class TestNoData:
    def test_no_snapshots_returns_zero_score(self):
        score = score_short_interest("GME", [], AS_OF)
        assert score.score == 0.0
        assert score.confidence == 0.0
        assert score.details["reason"] == "no_eligible_snapshots"

    def test_future_snapshots_filtered_out(self):
        future = _snap(settlement_date="2026-04-01")
        score = score_short_interest("GME", [future], AS_OF)
        assert score.score == 0.0
        assert score.details["reason"] == "no_eligible_snapshots"

    def test_zero_shares_outstanding(self):
        snap = _snap(shares_outstanding=0)
        score = score_short_interest("GME", [snap], AS_OF)
        # Should handle gracefully — SI% = 0
        assert 0.0 <= score.score <= 100.0
        assert score.details["short_interest_pct"] == 0.0

    def test_zero_avg_daily_volume(self):
        snap = _snap(avg_daily_volume=0)
        score = score_short_interest("GME", [snap], AS_OF)
        assert 0.0 <= score.score <= 100.0
        assert score.details["days_to_cover"] == 0.0

    def test_no_prior_short_interest(self):
        snap = _snap(prior_short_interest=None)
        score = score_short_interest("GME", [snap], AS_OF)
        assert 0.0 <= score.score <= 100.0
        assert score.details["short_interest_change_pct"] is None


# ── Level scoring ─────────────────────────────────────────────────────

class TestLevelScoring:
    def test_extreme_si_pct_scores_max_level(self):
        # 20%+ SI → 30 points
        snap = _snap(short_interest=25_000_000, shares_outstanding=100_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["level"] == 30.0

    def test_high_si_pct_scores_25(self):
        # 15% SI → 25 points
        snap = _snap(short_interest=15_000_000, shares_outstanding=100_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["level"] == 25.0

    def test_moderate_si_pct_scores_20(self):
        # 10% SI → 20 points
        snap = _snap(short_interest=10_000_000, shares_outstanding=100_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["level"] == 20.0

    def test_low_si_pct_scores_low(self):
        # 1% SI → 3 points (falls to lowest bucket)
        snap = _snap(short_interest=1_000_000, shares_outstanding=100_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["level"] == 3.0


# ── Trend scoring ─────────────────────────────────────────────────────

class TestTrendScoring:
    def test_massive_short_covering_is_bullish(self):
        # Prior 20M → Current 10M = -50% change → max bullish trend
        snap = _snap(short_interest=10_000_000, prior_short_interest=20_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 35.0

    def test_moderate_short_covering(self):
        # Prior 12M → Current 10M = -16.7% change
        snap = _snap(short_interest=10_000_000, prior_short_interest=12_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 24.0

    def test_shorts_increasing_is_bearish(self):
        # Prior 5M → Current 10M = +100% change → max bearish
        snap = _snap(short_interest=10_000_000, prior_short_interest=5_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 0.0

    def test_moderate_increase(self):
        # Prior 8M → Current 10M = +25% change
        snap = _snap(short_interest=10_000_000, prior_short_interest=8_000_000)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == 3.0

    def test_no_prior_data_is_neutral(self):
        snap = _snap(prior_short_interest=None)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["trend"] == DEFAULT_CONFIG.trend_neutral_score


# ── Days-to-Cover scoring ────────────────────────────────────────────

class TestDaysToCovertScoring:
    def test_extreme_dtc_scores_max(self):
        # 10M short / 500K daily vol = 20 DTC
        snap = _snap(short_interest=10_000_000, avg_daily_volume=500_000.0)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["dtc"] == 20.0

    def test_high_dtc_scores_16(self):
        # 7M short / 1M daily vol = 7 DTC
        snap = _snap(short_interest=7_000_000, avg_daily_volume=1_000_000.0)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["dtc"] == 16.0

    def test_low_dtc_scores_low(self):
        # 1M short / 5M daily vol = 0.2 DTC
        snap = _snap(short_interest=1_000_000, avg_daily_volume=5_000_000.0)
        score = score_short_interest("GME", [snap], AS_OF)
        assert score.details["sub_scores"]["dtc"] == 1.0


# ── Consistency scoring ──────────────────────────────────────────────

class TestConsistencyScoring:
    def test_single_snapshot_gets_no_data_score(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        assert score.details["sub_scores"]["consistency"] == DEFAULT_CONFIG.consistency_no_data_score

    def test_two_consistent_decreases_get_confirmed(self):
        # Two periods of shorts decreasing
        snaps = [
            _snap(
                settlement_date="2026-02-14",
                short_interest=14_000_000,
                prior_short_interest=16_000_000,
            ),
            _snap(
                settlement_date="2026-02-28",
                short_interest=12_000_000,
                prior_short_interest=14_000_000,
            ),
        ]
        score = score_short_interest("GME", snaps, AS_OF)
        assert score.details["sub_scores"]["consistency"] == DEFAULT_CONFIG.consistency_confirmed_score

    def test_two_inconsistent_directions_get_partial(self):
        # One increase, one decrease
        snaps = [
            _snap(
                settlement_date="2026-02-14",
                short_interest=14_000_000,
                prior_short_interest=12_000_000,  # increase
            ),
            _snap(
                settlement_date="2026-02-28",
                short_interest=12_000_000,
                prior_short_interest=14_000_000,  # decrease
            ),
        ]
        score = score_short_interest("GME", snaps, AS_OF)
        assert score.details["sub_scores"]["consistency"] == DEFAULT_CONFIG.consistency_partial_score


# ── Composite score scenarios ────────────────────────────────────────

class TestCompositeScenarios:
    def test_squeeze_candidate_scores_high(self):
        """High SI + large short covering + high DTC → very high score."""
        snap = _snap(
            short_interest=20_000_000,
            avg_daily_volume=1_500_000.0,
            shares_outstanding=100_000_000,
            prior_short_interest=30_000_000,  # -33% covering
        )
        score = score_short_interest("GME", [snap], AS_OF)
        # Level=30 + Trend=35 + DTC=20 + Consistency=5 = 90
        assert score.score >= 80.0

    def test_bearish_pileup_scores_low(self):
        """Rising shorts + moderate SI → lower score."""
        snap = _snap(
            short_interest=10_000_000,
            avg_daily_volume=5_000_000.0,
            shares_outstanding=100_000_000,
            prior_short_interest=5_000_000,  # +100% increase
        )
        score = score_short_interest("GME", [snap], AS_OF)
        # Level=20 + Trend=0 + DTC=4 + Consistency=5 = 29
        assert score.score <= 35.0

    def test_neutral_low_si_mid_score(self):
        """Low SI with no trend data → middling score."""
        snap = _snap(
            short_interest=2_000_000,
            avg_daily_volume=5_000_000.0,
            shares_outstanding=100_000_000,
            prior_short_interest=None,
        )
        score = score_short_interest("GME", [snap], AS_OF)
        assert 20.0 <= score.score <= 50.0


# ── Batch scoring ────────────────────────────────────────────────────

class TestBatchScoring:
    def test_batch_returns_dict_of_layer_scores(self):
        result = score_short_interest_batch(
            {
                "GME": [_snap(ticker="GME")],
                "AMC": [_snap(ticker="AMC")],
            },
            AS_OF,
        )
        assert len(result) == 2
        assert all(isinstance(v, LayerScore) for v in result.values())
        assert result["GME"].ticker == "GME"
        assert result["AMC"].ticker == "AMC"

    def test_batch_empty_input(self):
        result = score_short_interest_batch({}, AS_OF)
        assert result == {}


# ── FINRA client integration ─────────────────────────────────────────

class TestFINRAClientIntegration:
    def test_normalize_snapshots_produces_valid_input(self):
        raw = [
            {
                "settlement_date": "2026-02-28",
                "short_interest": 10_000_000,
                "avg_daily_volume": 5_000_000,
                "shares_outstanding": 100_000_000,
                "prior_short_interest": 12_000_000,
                "source_ref": "finra-api",
            }
        ]
        snaps = normalize_snapshots(raw, "GME")
        assert len(snaps) == 1
        assert snaps[0].ticker == "GME"
        assert snaps[0].short_interest_pct == 10.0

        # Feed into scorer
        score = score_short_interest("GME", snaps, AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L3_SHORT_INTEREST
        assert score.score > 0

    def test_normalize_bad_records_skipped(self):
        raw = [
            {"settlement_date": "2026-02-28"},  # missing short_interest
            {"short_interest": 5000},  # missing settlement_date
        ]
        snaps = normalize_snapshots(raw, "GME")
        assert len(snaps) == 0


# ── Detail keys alignment with layer registry ────────────────────────

class TestDetailKeysAlignment:
    """Verify the scorer emits detail keys matching the F-001 layer registry."""

    def test_all_registry_required_keys_present(self):
        from app.signal.layer_registry import get_layer_contract
        contract = get_layer_contract(LayerId.L3_SHORT_INTEREST)
        score = score_short_interest("GME", [_snap()], AS_OF)
        missing = score.missing_detail_keys(contract.required_detail_keys)
        assert missing == (), f"Missing required detail keys: {missing}"

    def test_freshness_evaluates_correctly(self):
        from app.signal.layer_registry import evaluate_freshness, FreshnessState
        score = score_short_interest("GME", [_snap()], AS_OF)
        # Score just created with as_of = AS_OF, reference = AS_OF → FRESH
        freshness = evaluate_freshness(score, AS_OF)
        assert freshness == FreshnessState.FRESH


# ── Provenance ───────────────────────────────────────────────────────

class TestProvenance:
    def test_provenance_ref_contains_ticker_and_date(self):
        score = score_short_interest("GME", [_snap()], AS_OF)
        assert "GME" in score.provenance_ref
        assert "2026-02-28" in score.provenance_ref

    def test_no_data_provenance_ref(self):
        score = score_short_interest("GME", [], AS_OF)
        assert "no-data" in score.provenance_ref
