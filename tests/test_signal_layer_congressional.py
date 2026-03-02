"""Tests for F-003: L5 Congressional Trading scorer."""

from __future__ import annotations

import pytest

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from app.signal.layers.congressional import (
    DEFAULT_CONFIG,
    CongressionalScoringConfig,
    score_congressional,
    score_congressional_batch,
)
from intelligence.capitol_trades_client import (
    Chamber,
    CongressionalTrade,
    TradeDirection,
    normalize_trades,
)


AS_OF = "2026-03-01T00:00:00Z"


def _trade(
    ticker: str = "NVDA",
    member_name: str = "Nancy Pelosi",
    chamber: Chamber = Chamber.HOUSE,
    direction: TradeDirection = TradeDirection.BUY,
    trade_date: str = "2026-02-15",
    disclosure_date: str = "2026-02-28",
    estimated_value_low: float = 100_000.0,
    estimated_value_high: float = 250_000.0,
    committee_memberships: tuple[str, ...] = (),
) -> CongressionalTrade:
    return CongressionalTrade(
        ticker=ticker,
        member_name=member_name,
        chamber=chamber,
        direction=direction,
        trade_date=trade_date,
        disclosure_date=disclosure_date,
        estimated_value_low=estimated_value_low,
        estimated_value_high=estimated_value_high,
        committee_memberships=committee_memberships,
    )


# ── LayerScore contract compliance ───────────────────────────────────

class TestLayerScoreContract:
    def test_returns_layer_score_with_correct_layer_id(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert isinstance(score, LayerScore)
        assert score.layer_id == LayerId.L5_CONGRESSIONAL

    def test_ticker_is_uppercased(self):
        score = score_congressional("nvda", [_trade(ticker="nvda")], AS_OF)
        assert score.ticker == "NVDA"

    def test_score_in_valid_range(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert 0.0 <= score.score <= 100.0

    def test_confidence_in_valid_range(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert score.confidence is not None
        assert 0.0 <= score.confidence <= 1.0

    def test_round_trip_dict_serialization(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        d = score.to_dict()
        restored = LayerScore.from_dict(d)
        assert restored.layer_id == score.layer_id
        assert restored.score == score.score

    def test_source_matches_config(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert score.source == DEFAULT_CONFIG.source

    def test_required_detail_keys_present(self):
        """All required keys from the layer registry contract."""
        score = score_congressional("NVDA", [_trade()], AS_OF)
        for key in ("filing_lag_days", "committee_relevance",
                     "net_trade_value", "filing_count"):
            assert key in score.details, f"Missing required detail key: {key}"


# ── No-data / edge cases ─────────────────────────────────────────────

class TestNoData:
    def test_no_trades_returns_zero_score(self):
        score = score_congressional("NVDA", [], AS_OF)
        assert score.score == 0.0
        assert score.confidence == 0.0
        assert score.details["reason"] == "no_trades_in_window"

    def test_old_trades_outside_window_filtered(self):
        old = _trade(trade_date="2020-01-01", disclosure_date="2020-02-01")
        score = score_congressional("NVDA", [old], AS_OF)
        assert score.score == 0.0

    def test_future_trades_filtered(self):
        future = _trade(trade_date="2026-06-01", disclosure_date="2026-07-01")
        score = score_congressional("NVDA", [future], AS_OF)
        assert score.score == 0.0


# ── Direction scoring ────────────────────────────────────────────────

class TestDirectionScoring:
    def test_all_buys_high_direction_score(self):
        trades = [
            _trade(member_name="Member A", direction=TradeDirection.BUY),
            _trade(member_name="Member B", direction=TradeDirection.BUY),
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        # 100% buy ratio → 30 points
        assert score.details["sub_scores"]["direction"] == 30.0

    def test_all_sells_low_direction_score(self):
        trades = [
            _trade(member_name="Member A", direction=TradeDirection.SELL),
            _trade(member_name="Member B", direction=TradeDirection.SELL),
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        # 0% buy ratio → 0 points
        assert score.details["sub_scores"]["direction"] == 0.0

    def test_mixed_direction(self):
        trades = [
            _trade(member_name="Member A", direction=TradeDirection.BUY,
                   estimated_value_low=100_000, estimated_value_high=200_000),
            _trade(member_name="Member B", direction=TradeDirection.SELL,
                   estimated_value_low=100_000, estimated_value_high=200_000),
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        # 50% buy ratio → 10 points
        assert score.details["sub_scores"]["direction"] == 10.0


# ── Cluster scoring ──────────────────────────────────────────────────

class TestClusterScoring:
    def test_single_member_low_cluster(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert score.details["sub_scores"]["cluster"] == 5.0

    def test_multiple_members_high_cluster(self):
        trades = [
            _trade(member_name=f"Member {i}", direction=TradeDirection.BUY)
            for i in range(5)
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        assert score.details["sub_scores"]["cluster"] == 25.0

    def test_three_members_moderate_cluster(self):
        trades = [
            _trade(member_name=f"Member {i}", direction=TradeDirection.BUY)
            for i in range(3)
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        assert score.details["sub_scores"]["cluster"] == 18.0


# ── Committee scoring ────────────────────────────────────────────────

class TestCommitteeScoring:
    def test_no_committee_memberships(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert score.details["sub_scores"]["committee"] == DEFAULT_CONFIG.committee_none_relevant_score

    def test_all_relevant_committees(self):
        trades = [
            _trade(
                member_name="Member A",
                committee_memberships=("Senate Banking Committee",),
            ),
            _trade(
                member_name="Member B",
                committee_memberships=("House Financial Services",),
            ),
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        assert score.details["sub_scores"]["committee"] == DEFAULT_CONFIG.committee_all_relevant_score

    def test_one_relevant_committee(self):
        trades = [
            _trade(
                member_name="Member A",
                committee_memberships=("Senate Armed Services",),
            ),
            _trade(
                member_name="Member B",
                committee_memberships=("Committee on the Judiciary",),
            ),
            _trade(
                member_name="Member C",
                committee_memberships=("Committee on Education",),
            ),
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        # 1 of 3 relevant → ratio 0.33 → one_relevant
        assert score.details["sub_scores"]["committee"] == DEFAULT_CONFIG.committee_one_relevant_score


# ── Recency scoring ──────────────────────────────────────────────────

class TestRecencyScoring:
    def test_fast_disclosure_high_recency(self):
        trade = _trade(trade_date="2026-02-20", disclosure_date="2026-02-25")
        score = score_congressional("NVDA", [trade], AS_OF)
        # 5 day lag → 20 points (<=15)
        assert score.details["sub_scores"]["recency"] == 20.0

    def test_slow_disclosure_low_recency(self):
        trade = _trade(trade_date="2026-01-01", disclosure_date="2026-02-25")
        score = score_congressional("NVDA", [trade], AS_OF)
        # 55 day lag → 8 points (<=60)
        assert score.details["sub_scores"]["recency"] == 8.0


# ── Composite scenarios ──────────────────────────────────────────────

class TestCompositeScenarios:
    def test_strong_cluster_buy_with_relevant_committees(self):
        """Multiple committee-relevant members buying → high score."""
        trades = [
            _trade(member_name=f"Senator {i}", direction=TradeDirection.BUY,
                   trade_date="2026-02-15", disclosure_date="2026-02-20",
                   committee_memberships=("Senate Banking Committee",))
            for i in range(5)
        ]
        score = score_congressional("NVDA", trades, AS_OF)
        assert score.score >= 80.0

    def test_single_sell_low_score(self):
        trade = _trade(
            direction=TradeDirection.SELL,
            trade_date="2026-01-01",
            disclosure_date="2026-02-15",
        )
        score = score_congressional("NVDA", [trade], AS_OF)
        assert score.score <= 30.0


# ── Batch scoring ────────────────────────────────────────────────────

class TestBatchScoring:
    def test_batch_returns_dict(self):
        result = score_congressional_batch(
            {
                "NVDA": [_trade(ticker="NVDA")],
                "AAPL": [_trade(ticker="AAPL")],
            },
            AS_OF,
        )
        assert len(result) == 2
        assert all(isinstance(v, LayerScore) for v in result.values())

    def test_batch_empty(self):
        result = score_congressional_batch({}, AS_OF)
        assert result == {}


# ── Capitol Trades client integration ────────────────────────────────

class TestCapitolTradesClientIntegration:
    def test_normalize_trades_produces_valid_input(self):
        raw = [
            {
                "member_name": "Nancy Pelosi",
                "chamber": "house",
                "direction": "buy",
                "trade_date": "2026-02-15",
                "disclosure_date": "2026-02-28",
                "estimated_value_low": 100_000,
                "estimated_value_high": 250_000,
                "committee_memberships": [],
            }
        ]
        trades = normalize_trades(raw, "NVDA")
        assert len(trades) == 1
        assert trades[0].ticker == "NVDA"

        score = score_congressional("NVDA", trades, AS_OF)
        assert isinstance(score, LayerScore)
        assert score.score > 0

    def test_normalize_bad_records_skipped(self):
        raw = [
            {"member_name": "Test"},  # missing required fields
        ]
        trades = normalize_trades(raw, "NVDA")
        assert len(trades) == 0


# ── Detail keys alignment with layer registry ────────────────────────

class TestDetailKeysAlignment:
    def test_all_registry_required_keys_present(self):
        from app.signal.layer_registry import get_layer_contract
        contract = get_layer_contract(LayerId.L5_CONGRESSIONAL)
        score = score_congressional("NVDA", [_trade()], AS_OF)
        missing = score.missing_detail_keys(contract.required_detail_keys)
        assert missing == (), f"Missing required detail keys: {missing}"

    def test_freshness_evaluates_correctly(self):
        from app.signal.layer_registry import evaluate_freshness, FreshnessState
        score = score_congressional("NVDA", [_trade()], AS_OF)
        freshness = evaluate_freshness(score, AS_OF)
        assert freshness == FreshnessState.FRESH


# ── Provenance ───────────────────────────────────────────────────────

class TestProvenance:
    def test_provenance_ref_contains_ticker(self):
        score = score_congressional("NVDA", [_trade()], AS_OF)
        assert "NVDA" in score.provenance_ref

    def test_no_data_provenance(self):
        score = score_congressional("NVDA", [], AS_OF)
        assert "no-data" in score.provenance_ref
