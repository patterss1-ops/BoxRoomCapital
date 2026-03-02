"""Tests for E-005: L4 Analyst Revisions scorer."""

from __future__ import annotations

import pytest

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from app.signal.layers.analyst_revisions import (
    DEFAULT_CONFIG,
    AnalystRevision,
    EstimateType,
    RevisionDirection,
    RevisionScoringConfig,
    score_analyst_revisions,
    score_revisions_batch,
)


AS_OF = "2026-03-01T00:00:00Z"


def _rev(
    ticker: str = "AAPL",
    analyst: str = "Goldman",
    direction: RevisionDirection = RevisionDirection.UP,
    estimate_type: EstimateType = EstimateType.EPS,
    old_estimate: float = 2.00,
    new_estimate: float = 2.20,
    revision_date: str = "2026-02-25",
) -> AnalystRevision:
    return AnalystRevision(
        ticker=ticker,
        analyst_name=analyst,
        direction=direction,
        estimate_type=estimate_type,
        old_estimate=old_estimate,
        new_estimate=new_estimate,
        revision_date=revision_date,
    )


def _down_rev(
    ticker: str = "AAPL",
    analyst: str = "Bear",
    old_estimate: float = 2.20,
    new_estimate: float = 1.80,
    revision_date: str = "2026-02-25",
) -> AnalystRevision:
    return AnalystRevision(
        ticker=ticker,
        analyst_name=analyst,
        direction=RevisionDirection.DOWN,
        estimate_type=EstimateType.EPS,
        old_estimate=old_estimate,
        new_estimate=new_estimate,
        revision_date=revision_date,
    )


# ── LayerScore contract compliance ───────────────────────────────────

class TestLayerScoreContract:
    def test_returns_layer_score_with_correct_layer_id(self):
        score = score_analyst_revisions("AAPL", [_rev()], AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L4_ANALYST_REVISIONS

    def test_ticker_is_uppercased(self):
        score = score_analyst_revisions("aapl", [_rev(ticker="aapl")], AS_OF)
        assert score.ticker == "AAPL"

    def test_score_in_valid_range(self):
        score = score_analyst_revisions("AAPL", [_rev()], AS_OF)
        assert 0.0 <= score.score <= 100.0

    def test_confidence_in_valid_range(self):
        score = score_analyst_revisions("AAPL", [_rev()], AS_OF)
        assert score.confidence is not None
        assert 0.0 <= score.confidence <= 1.0

    def test_round_trip_dict(self):
        score = score_analyst_revisions("AAPL", [_rev()], AS_OF)
        d = score.to_dict()
        restored = LayerScore.from_dict(d)
        assert restored.layer_id == score.layer_id
        assert restored.score == score.score


# ── Direction scoring ─────────────────────────────────────────────────

class TestDirectionScoring:
    def test_all_up_revisions_scores_max_direction(self):
        revs = [_rev(analyst=f"A{i}") for i in range(5)]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.details["sub_scores"]["direction"] == 35.0

    def test_all_down_revisions_scores_min_direction(self):
        revs = [_down_rev(analyst=f"A{i}") for i in range(5)]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.details["sub_scores"]["direction"] == 0.0

    def test_balanced_revisions_score_10(self):
        revs = [
            _rev(analyst="Bull1"),
            _down_rev(analyst="Bear1"),
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.details["sub_scores"]["direction"] == 10.0

    def test_60pct_net_up_scores_28(self):
        # 4 up, 1 down = net fraction 0.6
        revs = [
            _rev(analyst="A1"), _rev(analyst="A2"),
            _rev(analyst="A3"), _rev(analyst="A4"),
            _down_rev(analyst="A5"),
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.details["sub_scores"]["direction"] == 28.0

    def test_maintained_counts_as_revision(self):
        revs = [
            _rev(analyst="Bull"),
            AnalystRevision(
                ticker="AAPL", analyst_name="Neutral",
                direction=RevisionDirection.MAINTAINED,
                estimate_type=EstimateType.EPS,
                old_estimate=2.0, new_estimate=2.0,
                revision_date="2026-02-20",
            ),
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        # 1 up, 0 down, 1 maintained = net fraction 0.5
        assert score.details["net_direction_fraction"] == 0.5


# ── Magnitude scoring ─────────────────────────────────────────────────

class TestMagnitudeScoring:
    def test_large_revision_25pct_scores_30(self):
        rev = _rev(old_estimate=2.0, new_estimate=2.50)  # 25% change
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.details["sub_scores"]["magnitude"] == 30.0

    def test_moderate_revision_10pct_scores_24(self):
        rev = _rev(old_estimate=2.0, new_estimate=2.20)  # 10% change
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.details["sub_scores"]["magnitude"] == 24.0

    def test_small_revision_2pct_scores_12(self):
        rev = _rev(old_estimate=2.0, new_estimate=2.04)  # 2% change
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.details["sub_scores"]["magnitude"] == 12.0

    def test_zero_old_estimate_returns_0(self):
        rev = _rev(old_estimate=0.0, new_estimate=1.0)
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        # change_pct is None for zero old estimate
        assert score.details["sub_scores"]["magnitude"] == 0.0

    def test_down_revisions_magnitude_is_absolute(self):
        """Magnitude uses absolute change regardless of direction."""
        rev = _down_rev(old_estimate=2.0, new_estimate=1.50)  # -25% change
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.details["sub_scores"]["magnitude"] == 30.0


# ── Breadth scoring ───────────────────────────────────────────────────

class TestBreadthScoring:
    def test_all_analysts_agree_scores_max_breadth(self):
        revs = [_rev(analyst=f"A{i}") for i in range(5)]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.details["sub_scores"]["breadth"] == 20.0

    def test_60pct_agreement_scores_15(self):
        # 3 up, 2 down = 3/5 agreeing (60% of distinct analysts)
        revs = [
            _rev(analyst="A1"), _rev(analyst="A2"), _rev(analyst="A3"),
            _down_rev(analyst="A4"), _down_rev(analyst="A5"),
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.details["sub_scores"]["breadth"] == 15.0

    def test_single_analyst_counts_as_100pct(self):
        score = score_analyst_revisions("AAPL", [_rev()], AS_OF)
        assert score.details["sub_scores"]["breadth"] == 20.0


# ── Recency scoring ──────────────────────────────────────────────────

class TestRecencyScoring:
    def test_recent_revision_scores_15(self):
        rev = _rev(revision_date="2026-02-28")
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.details["sub_scores"]["recency"] == 15.0

    def test_30_day_old_revision_scores_9(self):
        rev = _rev(revision_date="2026-02-01")
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.details["sub_scores"]["recency"] == 9.0

    def test_outside_window_excluded(self):
        rev = _rev(revision_date="2025-11-01")  # >90 days
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.score == 0.0
        assert score.details["reason"] == "no_revisions_in_window"


# ── Composite score ───────────────────────────────────────────────────

class TestCompositeScore:
    def test_maximum_possible_score(self):
        """Multiple analysts, large revisions, all up, very recent."""
        revs = [
            _rev(analyst=f"A{i}", old_estimate=2.0, new_estimate=2.50,
                 revision_date="2026-02-28")
            for i in range(5)
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        # direction=35 + magnitude=30 + breadth=20 + recency=15 = 100
        assert score.score == 100.0

    def test_no_revisions_scores_zero(self):
        score = score_analyst_revisions("AAPL", [], AS_OF)
        assert score.score == 0.0
        assert score.confidence == 0.0

    def test_mixed_sentiment_mid_range(self):
        revs = [
            _rev(analyst="Bull1", old_estimate=2.0, new_estimate=2.10),
            _rev(analyst="Bull2", old_estimate=2.0, new_estimate=2.05),
            _down_rev(analyst="Bear1"),
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert 20.0 < score.score < 80.0

    def test_score_capped_at_100(self):
        revs = [
            _rev(analyst=f"A{i}", old_estimate=1.0, new_estimate=10.0,
                 revision_date="2026-02-28")
            for i in range(20)
        ]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        assert score.score <= 100.0


# ── Details and auditability ──────────────────────────────────────────

class TestDetails:
    def test_details_contains_all_fields(self):
        revs = [_rev(), _down_rev(analyst="Bear")]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        d = score.details
        assert "total_revisions" in d
        assert "ups" in d
        assert "downs" in d
        assert "maintained" in d
        assert "distinct_analysts" in d
        assert "net_direction_fraction" in d
        assert "sub_scores" in d
        assert set(d["sub_scores"].keys()) == {"direction", "magnitude", "breadth", "recency"}
        assert "raw_score" in d

    def test_sub_scores_sum_to_raw(self):
        revs = [_rev(), _rev(analyst="B")]
        score = score_analyst_revisions("AAPL", revs, AS_OF)
        sub = score.details["sub_scores"]
        expected = sub["direction"] + sub["magnitude"] + sub["breadth"] + sub["recency"]
        assert score.details["raw_score"] == expected


# ── Window filtering ──────────────────────────────────────────────────

class TestWindowFiltering:
    def test_future_revisions_excluded(self):
        rev = _rev(revision_date="2026-04-01")
        score = score_analyst_revisions("AAPL", [rev], AS_OF)
        assert score.score == 0.0

    def test_custom_window(self):
        config = RevisionScoringConfig(window_days=30)
        rev = _rev(revision_date="2026-01-15")  # >30 days ago
        score = score_analyst_revisions("AAPL", [rev], AS_OF, config=config)
        assert score.score == 0.0


# ── Batch scoring ─────────────────────────────────────────────────────

class TestBatchScoring:
    def test_batch_scores_multiple_tickers(self):
        results = score_revisions_batch(
            {
                "AAPL": [_rev(ticker="AAPL")],
                "MSFT": [_rev(ticker="MSFT", analyst="MS")],
            },
            as_of=AS_OF,
        )
        assert set(results.keys()) == {"AAPL", "MSFT"}
        for ticker, score in results.items():
            assert score.ticker == ticker
            assert score.layer_id == LayerId.L4_ANALYST_REVISIONS

    def test_empty_batch_returns_empty(self):
        assert score_revisions_batch({}, as_of=AS_OF) == {}


# ── AnalystRevision contract ─────────────────────────────────────────

class TestAnalystRevision:
    def test_change_pct(self):
        rev = _rev(old_estimate=2.0, new_estimate=2.20)
        assert rev.change_pct == pytest.approx(10.0)

    def test_change_pct_negative(self):
        rev = _down_rev(old_estimate=2.0, new_estimate=1.80)
        assert rev.change_pct == pytest.approx(-10.0)

    def test_change_pct_zero_old(self):
        rev = _rev(old_estimate=0.0, new_estimate=1.0)
        assert rev.change_pct is None

    def test_revision_datetime_parses(self):
        rev = _rev(revision_date="2026-02-25T14:30:00Z")
        dt = rev.revision_datetime
        assert dt.year == 2026
        assert dt.hour == 14
