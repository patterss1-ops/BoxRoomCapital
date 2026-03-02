"""Tests for E-004: L1 PEAD scorer."""

from __future__ import annotations

import pytest

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from app.signal.layers.pead import (
    DEFAULT_CONFIG,
    EarningsSurprise,
    GuidanceDirection,
    PEADScoringConfig,
    score_pead,
    score_pead_batch,
)


AS_OF = "2026-03-01T00:00:00Z"


def _surprise(
    ticker: str = "AAPL",
    earnings_date: str = "2026-02-20",
    actual_eps: float = 2.50,
    consensus_eps: float = 2.00,
    actual_revenue: float = None,
    consensus_revenue: float = None,
    guidance: GuidanceDirection = GuidanceDirection.NONE,
) -> EarningsSurprise:
    return EarningsSurprise(
        ticker=ticker,
        earnings_date=earnings_date,
        actual_eps=actual_eps,
        consensus_eps=consensus_eps,
        actual_revenue=actual_revenue,
        consensus_revenue=consensus_revenue,
        guidance=guidance,
    )


# ── LayerScore contract compliance ───────────────────────────────────

class TestLayerScoreContract:
    def test_returns_layer_score_with_correct_layer_id(self):
        score = score_pead("AAPL", [_surprise()], AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L1_PEAD

    def test_ticker_is_uppercased(self):
        score = score_pead("aapl", [_surprise(ticker="aapl")], AS_OF)
        assert score.ticker == "AAPL"

    def test_score_in_valid_range(self):
        score = score_pead("AAPL", [_surprise()], AS_OF)
        assert 0.0 <= score.score <= 100.0

    def test_confidence_in_valid_range(self):
        score = score_pead("AAPL", [_surprise()], AS_OF)
        assert score.confidence is not None
        assert 0.0 <= score.confidence <= 1.0

    def test_round_trip_dict_serialization(self):
        score = score_pead("AAPL", [_surprise()], AS_OF)
        d = score.to_dict()
        restored = LayerScore.from_dict(d)
        assert restored.layer_id == score.layer_id
        assert restored.score == score.score


# ── SUE scoring ──────────────────────────────────────────────────────

class TestSUEScoring:
    def test_massive_beat_scores_sue_60(self):
        # 50%+ surprise: actual=3.0, consensus=2.0 -> 50% beat
        s = _surprise(actual_eps=3.0, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 60.0

    def test_large_beat_scores_sue_50(self):
        # 25-49% surprise: actual=2.5, consensus=2.0 -> 25% beat
        s = _surprise(actual_eps=2.5, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 50.0

    def test_solid_beat_scores_sue_40(self):
        # 10-24% surprise: actual=2.2, consensus=2.0 -> 10% beat
        s = _surprise(actual_eps=2.2, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 40.0

    def test_small_beat_scores_sue_20(self):
        # 1-4% surprise
        s = _surprise(actual_eps=2.02, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 20.0

    def test_inline_scores_sue_10(self):
        s = _surprise(actual_eps=2.0, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 10.0

    def test_small_miss_scores_sue_10(self):
        # -1% to 0%
        s = _surprise(actual_eps=1.99, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 10.0

    def test_moderate_miss_scores_sue_8(self):
        # -5% miss
        s = _surprise(actual_eps=1.90, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 8.0

    def test_large_miss_scores_sue_5(self):
        # -20% miss: actual=1.6, consensus=2.0 -> -20%
        s = _surprise(actual_eps=1.60, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 5.0

    def test_catastrophic_miss_scores_sue_0(self):
        # -50%+ miss
        s = _surprise(actual_eps=0.50, consensus_eps=2.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 0.0

    def test_zero_consensus_returns_sue_0(self):
        # Cannot compute surprise_pct when consensus is 0
        s = _surprise(actual_eps=1.0, consensus_eps=0.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["sue"] == 0.0


# ── Revenue scoring ──────────────────────────────────────────────────

class TestRevenueScoring:
    def test_revenue_beat_10pct_scores_20(self):
        s = _surprise(actual_revenue=110.0, consensus_revenue=100.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["revenue"] == 20.0

    def test_revenue_beat_5pct_scores_15(self):
        s = _surprise(actual_revenue=105.0, consensus_revenue=100.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["revenue"] == 15.0

    def test_no_revenue_data_scores_5_neutral(self):
        s = _surprise()  # no revenue data
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["revenue"] == 5.0

    def test_revenue_miss_10pct_scores_0(self):
        s = _surprise(actual_revenue=90.0, consensus_revenue=100.0)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["revenue"] == 0.0


# ── Guidance scoring ─────────────────────────────────────────────────

class TestGuidanceScoring:
    def test_raised_guidance_scores_20(self):
        s = _surprise(guidance=GuidanceDirection.RAISED)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["guidance"] == 20.0

    def test_maintained_guidance_scores_10(self):
        s = _surprise(guidance=GuidanceDirection.MAINTAINED)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["guidance"] == 10.0

    def test_lowered_guidance_scores_0(self):
        s = _surprise(guidance=GuidanceDirection.LOWERED)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["guidance"] == 0.0

    def test_no_guidance_scores_5(self):
        s = _surprise(guidance=GuidanceDirection.NONE)
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["sub_scores"]["guidance"] == 5.0


# ── Temporal decay ───────────────────────────────────────────────────

class TestDecay:
    def test_same_day_earnings_no_decay(self):
        s = _surprise(earnings_date="2026-03-01")
        score = score_pead("AAPL", [s], AS_OF)
        assert score.details["decay_factor"] == 1.0

    def test_30_days_ago_half_decay(self):
        s = _surprise(earnings_date="2026-01-30")
        score = score_pead("AAPL", [s], AS_OF)
        assert 0.45 <= score.details["decay_factor"] <= 0.55

    def test_60_days_ago_fully_decayed(self):
        # Exactly 60 days: decay_factor = 0.0, score = 0.0
        s = _surprise(earnings_date="2025-12-31")
        score = score_pead("AAPL", [s], AS_OF)
        assert score.score == 0.0
        assert score.details["decay_factor"] == 0.0

    def test_future_earnings_not_scored(self):
        s = _surprise(earnings_date="2026-04-01")
        score = score_pead("AAPL", [s], AS_OF)
        assert score.score == 0.0

    def test_decay_reduces_final_score(self):
        # Same surprise, different dates
        recent = _surprise(earnings_date="2026-02-28")
        older = _surprise(earnings_date="2026-02-01")
        score_recent = score_pead("AAPL", [recent], AS_OF)
        score_older = score_pead("AAPL", [older], AS_OF)
        assert score_recent.score > score_older.score


# ── Edge cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_no_earnings_data_scores_zero(self):
        score = score_pead("AAPL", [], AS_OF)
        assert score.score == 0.0
        assert score.confidence == 0.0
        assert score.details["reason"] == "no_eligible_earnings"

    def test_multiple_earnings_uses_most_recent(self):
        old = _surprise(earnings_date="2026-01-15", actual_eps=2.1, consensus_eps=2.0)
        new = _surprise(earnings_date="2026-02-20", actual_eps=3.0, consensus_eps=2.0)
        score = score_pead("AAPL", [old, new], AS_OF)
        # Should use the Feb 20 earnings (50% beat = SUE 60)
        assert score.details["earnings_date"] == "2026-02-20"
        assert score.details["sub_scores"]["sue"] == 60.0

    def test_maximum_possible_score(self):
        """Massive beat + revenue beat + raised guidance, same day."""
        s = _surprise(
            earnings_date="2026-03-01",
            actual_eps=3.0,
            consensus_eps=2.0,
            actual_revenue=120.0,
            consensus_revenue=100.0,
            guidance=GuidanceDirection.RAISED,
        )
        score = score_pead("AAPL", [s], AS_OF)
        # SUE=60 + revenue=20 + guidance=20 = 100, decay=1.0
        assert score.score == 100.0

    def test_score_capped_at_100(self):
        s = _surprise(
            earnings_date="2026-03-01",
            actual_eps=100.0,
            consensus_eps=1.0,
            actual_revenue=1000.0,
            consensus_revenue=100.0,
            guidance=GuidanceDirection.RAISED,
        )
        score = score_pead("AAPL", [s], AS_OF)
        assert score.score <= 100.0


# ── Details and auditability ──────────────────────────────────────────

class TestDetails:
    def test_details_contains_all_fields(self):
        s = _surprise(actual_revenue=105.0, consensus_revenue=100.0)
        score = score_pead("AAPL", [s], AS_OF)
        d = score.details
        assert "earnings_date" in d
        assert "actual_eps" in d
        assert "consensus_eps" in d
        assert "eps_surprise_pct" in d
        assert "revenue_surprise_pct" in d
        assert "guidance" in d
        assert "sub_scores" in d
        assert "raw_score" in d
        assert "decay_factor" in d
        assert "days_since_earnings" in d

    def test_sub_scores_sum_to_raw(self):
        s = _surprise(actual_revenue=110.0, consensus_revenue=100.0,
                       guidance=GuidanceDirection.RAISED)
        score = score_pead("AAPL", [s], AS_OF)
        sub = score.details["sub_scores"]
        expected_raw = sub["sue"] + sub["revenue"] + sub["guidance"]
        assert score.details["raw_score"] == expected_raw


# ── Batch scoring ─────────────────────────────────────────────────────

class TestBatchScoring:
    def test_batch_scores_multiple_tickers(self):
        results = score_pead_batch(
            {
                "AAPL": [_surprise(ticker="AAPL")],
                "MSFT": [_surprise(ticker="MSFT", actual_eps=1.5, consensus_eps=1.0)],
            },
            as_of=AS_OF,
        )
        assert set(results.keys()) == {"AAPL", "MSFT"}
        for ticker, score in results.items():
            assert score.ticker == ticker
            assert score.layer_id == LayerId.L1_PEAD

    def test_empty_batch_returns_empty(self):
        results = score_pead_batch({}, as_of=AS_OF)
        assert results == {}


# ── EarningsSurprise contract ─────────────────────────────────────────

class TestEarningsSurprise:
    def test_eps_surprise_pct(self):
        s = _surprise(actual_eps=2.5, consensus_eps=2.0)
        assert s.eps_surprise_pct == pytest.approx(25.0)

    def test_eps_surprise_pct_negative(self):
        s = _surprise(actual_eps=1.5, consensus_eps=2.0)
        assert s.eps_surprise_pct == pytest.approx(-25.0)

    def test_eps_surprise_pct_zero_consensus(self):
        s = _surprise(actual_eps=1.0, consensus_eps=0.0)
        assert s.eps_surprise_pct is None

    def test_revenue_surprise_pct(self):
        s = _surprise(actual_revenue=110.0, consensus_revenue=100.0)
        assert s.revenue_surprise_pct == pytest.approx(10.0)

    def test_revenue_surprise_pct_no_data(self):
        s = _surprise()
        assert s.revenue_surprise_pct is None
