"""Tests for E-002: L2 Insider Buying signal adapter."""

from __future__ import annotations

import pytest

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from intelligence.insider_signal_adapter import (
    DEFAULT_CONFIG,
    InsiderRole,
    InsiderScoringConfig,
    InsiderTransaction,
    TransactionType,
    score_batch,
    score_insider_activity,
)


AS_OF = "2026-03-01T00:00:00Z"


def _buy(
    ticker: str = "AAPL",
    insider: str = "Tim Cook",
    role: InsiderRole = InsiderRole.CEO,
    shares: float = 10_000,
    price: float = 175.0,
    filing_date: str = "2026-02-25",
    source_ref: str = "",
) -> InsiderTransaction:
    return InsiderTransaction(
        ticker=ticker,
        insider_name=insider,
        role=role,
        transaction_type=TransactionType.PURCHASE,
        shares=shares,
        price_per_share=price,
        filing_date=filing_date,
        source_ref=source_ref,
    )


def _sell(
    ticker: str = "AAPL",
    insider: str = "Jane Doe",
    role: InsiderRole = InsiderRole.VP,
    shares: float = 50_000,
    price: float = 175.0,
    filing_date: str = "2026-02-20",
) -> InsiderTransaction:
    return InsiderTransaction(
        ticker=ticker,
        insider_name=insider,
        role=role,
        transaction_type=TransactionType.SALE,
        shares=shares,
        price_per_share=price,
        filing_date=filing_date,
    )


# ── LayerScore contract compliance ───────────────────────────────────

class TestLayerScoreContract:
    """Verify the adapter produces valid LayerScore payloads."""

    def test_returns_layer_score_with_correct_layer_id(self):
        score, vetoes = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L2_INSIDER

    def test_ticker_is_uppercased(self):
        score, _ = score_insider_activity("aapl", [_buy(ticker="aapl")], AS_OF)
        assert score.ticker == "AAPL"

    def test_score_in_valid_range(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert 0.0 <= score.score <= 100.0

    def test_confidence_in_valid_range(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert score.confidence is not None
        assert 0.0 <= score.confidence <= 1.0

    def test_as_of_matches_input(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert score.as_of == AS_OF

    def test_source_matches_config(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert score.source == "insider-alpha-radar"

    def test_round_trip_dict_serialization(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        d = score.to_dict()
        restored = LayerScore.from_dict(d)
        assert restored.layer_id == score.layer_id
        assert restored.ticker == score.ticker
        assert restored.score == score.score

    def test_provenance_ref_is_deterministic(self):
        score1, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        score2, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert score1.provenance_ref == score2.provenance_ref
        assert score1.provenance_ref.startswith("insider-AAPL-")


# ── Cluster scoring ──────────────────────────────────────────────────

class TestClusterScoring:
    """Cluster sub-score: more distinct insiders buying = higher score."""

    def test_no_purchases_scores_zero(self):
        score, _ = score_insider_activity("AAPL", [], AS_OF)
        assert score.score == 0.0

    def test_single_insider_scores_cluster_10(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        assert score.details["sub_scores"]["cluster"] == 10.0

    def test_two_distinct_insiders_scores_cluster_20(self):
        txns = [
            _buy(insider="Alice"),
            _buy(insider="Bob", role=InsiderRole.CFO),
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["sub_scores"]["cluster"] == 20.0

    def test_three_insiders_scores_cluster_30(self):
        txns = [
            _buy(insider="Alice"),
            _buy(insider="Bob", role=InsiderRole.CFO),
            _buy(insider="Carol", role=InsiderRole.DIRECTOR),
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["sub_scores"]["cluster"] == 30.0

    def test_five_insiders_scores_cluster_40(self):
        txns = [
            _buy(insider=f"Person{i}", role=InsiderRole.DIRECTOR)
            for i in range(5)
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["sub_scores"]["cluster"] == 40.0

    def test_same_insider_multiple_purchases_counts_as_one(self):
        txns = [
            _buy(insider="Tim Cook", filing_date="2026-02-20"),
            _buy(insider="Tim Cook", filing_date="2026-02-25"),
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["cluster_count"] == 1
        assert score.details["sub_scores"]["cluster"] == 10.0


# ── Seniority scoring ────────────────────────────────────────────────

class TestSeniorityScoring:
    """Seniority sub-score: C-suite purchases rank highest."""

    def test_ceo_purchase_scores_25(self):
        score, _ = score_insider_activity("AAPL", [_buy(role=InsiderRole.CEO)], AS_OF)
        assert score.details["sub_scores"]["seniority"] == 25.0

    def test_director_purchase_scores_15(self):
        score, _ = score_insider_activity(
            "AAPL", [_buy(role=InsiderRole.DIRECTOR)], AS_OF
        )
        assert score.details["sub_scores"]["seniority"] == 15.0

    def test_highest_seniority_wins(self):
        txns = [
            _buy(insider="A", role=InsiderRole.OFFICER),
            _buy(insider="B", role=InsiderRole.CEO),
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["sub_scores"]["seniority"] == 25.0

    def test_ten_pct_owner_scores_12(self):
        score, _ = score_insider_activity(
            "AAPL", [_buy(role=InsiderRole.TEN_PCT_OWNER)], AS_OF
        )
        assert score.details["sub_scores"]["seniority"] == 12.0


# ── Conviction scoring ───────────────────────────────────────────────

class TestConvictionScoring:
    """Conviction sub-score: larger purchases score higher."""

    def test_million_dollar_purchase_scores_20(self):
        txn = _buy(shares=10_000, price=100.0)  # $1M
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["sub_scores"]["conviction"] == 20.0

    def test_500k_purchase_scores_16(self):
        txn = _buy(shares=5_000, price=100.0)  # $500K
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["sub_scores"]["conviction"] == 16.0

    def test_small_purchase_scores_4(self):
        txn = _buy(shares=100, price=50.0)  # $5K
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["sub_scores"]["conviction"] == 4.0

    def test_largest_purchase_determines_score(self):
        txns = [
            _buy(insider="Small", shares=100, price=50.0),        # $5K
            _buy(insider="Big", shares=10_000, price=100.0),      # $1M
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["sub_scores"]["conviction"] == 20.0


# ── Recency scoring ──────────────────────────────────────────────────

class TestRecencyScoring:
    """Recency sub-score: more recent filings score higher."""

    def test_purchase_within_7_days_scores_15(self):
        txn = _buy(filing_date="2026-02-28")  # 1 day ago from AS_OF
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["sub_scores"]["recency"] == 15.0

    def test_purchase_30_days_ago_scores_9(self):
        txn = _buy(filing_date="2026-02-01")  # ~28 days ago
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["sub_scores"]["recency"] == 9.0

    def test_purchase_75_days_ago_scores_3(self):
        txn = _buy(filing_date="2025-12-17")  # ~74 days ago
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["sub_scores"]["recency"] == 3.0

    def test_purchase_outside_window_excluded(self):
        txn = _buy(filing_date="2025-11-01")  # >90 days ago
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.score == 0.0
        assert score.details["total_purchases"] == 0

    def test_most_recent_purchase_determines_recency(self):
        txns = [
            _buy(insider="Old", filing_date="2026-01-05"),   # ~55 days ago
            _buy(insider="New", filing_date="2026-02-27"),   # 2 days ago
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.details["sub_scores"]["recency"] == 15.0


# ── Sell veto ─────────────────────────────────────────────────────────

class TestSellVeto:
    """Net-sell veto: aggregate selling > buying forces score to 0."""

    def test_sell_veto_when_selling_exceeds_buying(self):
        txns = [
            _buy(insider="Buyer", shares=100, price=100.0),       # $10K buy
            _sell(insider="Seller", shares=1000, price=100.0),    # $100K sell
        ]
        score, vetoes = score_insider_activity("AAPL", txns, AS_OF)
        assert score.score == 0.0
        assert "insider_sell_cluster" in vetoes
        assert score.details["vetoed"] is True

    def test_no_veto_when_buying_exceeds_selling(self):
        txns = [
            _buy(insider="BigBuyer", shares=10_000, price=100.0),  # $1M buy
            _sell(insider="SmallSeller", shares=100, price=100.0), # $10K sell
        ]
        score, vetoes = score_insider_activity("AAPL", txns, AS_OF)
        assert score.score > 0
        assert vetoes == []
        assert score.details["vetoed"] is False

    def test_veto_disabled_by_config(self):
        config = InsiderScoringConfig(sell_veto_enabled=False)
        txns = [
            _buy(insider="Buyer", shares=100, price=100.0),
            _sell(insider="Seller", shares=1000, price=100.0),
        ]
        score, vetoes = score_insider_activity("AAPL", txns, AS_OF, config=config)
        assert score.score > 0
        assert vetoes == []

    def test_sells_only_no_veto_just_zero(self):
        """Sells with no buys = score 0 but no veto (nothing to veto)."""
        txns = [_sell()]
        score, vetoes = score_insider_activity("AAPL", txns, AS_OF)
        assert score.score == 0.0
        assert vetoes == []


# ── Composite score ───────────────────────────────────────────────────

class TestCompositeScore:
    """Full composite score = cluster + seniority + conviction + recency."""

    def test_maximum_possible_score(self):
        """5+ distinct C-suite insiders, $1M+ purchases, within 7 days."""
        txns = [
            _buy(insider=f"CEO{i}", role=InsiderRole.CEO,
                 shares=10_000, price=100.0, filing_date="2026-02-28")
            for i in range(5)
        ]
        score, vetoes = score_insider_activity("AAPL", txns, AS_OF)
        # cluster=40 + seniority=25 + conviction=20 + recency=15 = 100
        assert score.score == 100.0
        assert vetoes == []

    def test_moderate_activity_scores_mid_range(self):
        """2 insiders, director-level, $200K, 20 days ago."""
        txns = [
            _buy(insider="Dir1", role=InsiderRole.DIRECTOR,
                 shares=1_000, price=200.0, filing_date="2026-02-10"),
            _buy(insider="Dir2", role=InsiderRole.DIRECTOR,
                 shares=500, price=200.0, filing_date="2026-02-10"),
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        # cluster=20 + seniority=15 + conviction=12 + recency=9 = 56
        assert score.score == 56.0

    def test_score_capped_at_100(self):
        """Even with extreme inputs, score does not exceed 100."""
        txns = [
            _buy(insider=f"CEO{i}", role=InsiderRole.CEO,
                 shares=100_000, price=1000.0, filing_date="2026-02-28")
            for i in range(10)
        ]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        assert score.score <= 100.0


# ── Details and auditability ──────────────────────────────────────────

class TestDetails:
    """Verify details dict contains all expected audit fields."""

    def test_details_contains_sub_scores(self):
        score, _ = score_insider_activity("AAPL", [_buy()], AS_OF)
        d = score.details
        assert "sub_scores" in d
        assert set(d["sub_scores"].keys()) == {"cluster", "seniority", "conviction", "recency"}

    def test_details_contains_counts_and_values(self):
        txns = [_buy(), _sell()]
        score, _ = score_insider_activity("AAPL", txns, AS_OF)
        d = score.details
        assert d["total_purchases"] == 1
        assert d["total_sales"] == 1
        assert d["total_buy_value"] > 0
        assert d["total_sell_value"] > 0
        assert "cluster_count" in d
        assert "raw_score" in d
        assert "vetoed" in d


# ── Window filtering ──────────────────────────────────────────────────

class TestWindowFiltering:
    """Transactions outside the evaluation window are excluded."""

    def test_future_transactions_excluded(self):
        txn = _buy(filing_date="2026-03-15")  # 15 days in future from AS_OF
        score, _ = score_insider_activity("AAPL", [txn], AS_OF)
        assert score.details["total_purchases"] == 0
        assert score.score == 0.0

    def test_custom_window(self):
        config = InsiderScoringConfig(window_days=30)
        txn = _buy(filing_date="2026-01-15")  # ~45 days ago — outside 30-day window
        score, _ = score_insider_activity("AAPL", [txn], AS_OF, config=config)
        assert score.details["total_purchases"] == 0

    def test_boundary_day_included(self):
        """Transaction on exactly the cutoff day is included."""
        config = InsiderScoringConfig(window_days=90)
        # Exactly 90 days before 2026-03-01 is 2025-12-02
        txn = _buy(filing_date="2025-12-01T00:00:00Z")
        score, _ = score_insider_activity("AAPL", [txn], AS_OF, config=config)
        assert score.details["total_purchases"] == 1


# ── Batch scoring ─────────────────────────────────────────────────────

class TestBatchScoring:
    """score_batch() runs multiple tickers in one call."""

    def test_batch_scores_multiple_tickers(self):
        results = score_batch(
            {
                "AAPL": [_buy(ticker="AAPL")],
                "MSFT": [_buy(ticker="MSFT", insider="Satya")],
            },
            as_of=AS_OF,
        )
        assert set(results.keys()) == {"AAPL", "MSFT"}
        for ticker, (score, vetoes) in results.items():
            assert score.ticker == ticker
            assert score.layer_id == LayerId.L2_INSIDER

    def test_empty_batch_returns_empty(self):
        results = score_batch({}, as_of=AS_OF)
        assert results == {}

    def test_batch_with_mixed_activity(self):
        results = score_batch(
            {
                "GOOD": [
                    _buy(ticker="GOOD", insider="CEO", role=InsiderRole.CEO,
                         shares=10_000, price=100.0),
                ],
                "BAD": [
                    _buy(ticker="BAD", insider="Buyer", shares=10, price=10.0),
                    _sell(ticker="BAD", insider="BigSeller", shares=10_000, price=100.0),
                ],
            },
            as_of=AS_OF,
        )
        good_score, good_vetoes = results["GOOD"]
        bad_score, bad_vetoes = results["BAD"]
        assert good_score.score > 0
        assert bad_score.score == 0.0
        assert "insider_sell_cluster" in bad_vetoes


# ── InsiderTransaction contract ───────────────────────────────────────

class TestInsiderTransaction:
    """Input dataclass validation."""

    def test_value_property(self):
        txn = _buy(shares=1000, price=150.0)
        assert txn.value == 150_000.0

    def test_filing_datetime_parses_date_only(self):
        txn = _buy(filing_date="2026-02-25")
        dt = txn.filing_datetime
        assert dt.year == 2026
        assert dt.month == 2
        assert dt.day == 25

    def test_filing_datetime_parses_iso_with_tz(self):
        txn = _buy(filing_date="2026-02-25T10:30:00Z")
        dt = txn.filing_datetime
        assert dt.year == 2026
        assert dt.hour == 10
