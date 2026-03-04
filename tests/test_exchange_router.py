"""Tests for M-004 multi-exchange order router."""

from __future__ import annotations

from execution.exchange_router import ExchangeRouter, RouteRequest, VenueSnapshot


def _seed_router() -> ExchangeRouter:
    router = ExchangeRouter(latency_weight=0.5, fill_rate_weight=0.3, cost_weight=0.2)
    router.update_snapshot(VenueSnapshot("XNAS", latency_ms=12, fill_rate=0.92, fee_bps=0.8, slippage_bps=0.3))
    router.update_snapshot(VenueSnapshot("BATS", latency_ms=8, fill_rate=0.88, fee_bps=1.1, slippage_bps=0.2))
    router.update_snapshot(VenueSnapshot("NYSE", latency_ms=20, fill_rate=0.97, fee_bps=0.5, slippage_bps=0.4))
    return router


def test_select_venue_returns_allowed_decision():
    router = _seed_router()
    req = RouteRequest(symbol="AAPL", side="buy", qty=100)
    decision = router.select_venue(req)
    assert decision.allowed is True
    assert decision.venue in {"XNAS", "BATS", "NYSE"}
    assert "total" in decision.score_breakdown[decision.venue]


def test_allowlist_restricts_candidates():
    router = _seed_router()
    req = RouteRequest(symbol="AAPL", side="sell", qty=25, allowed_venues=["NYSE"])
    decision = router.select_venue(req)
    assert decision.allowed is True
    assert decision.venue == "NYSE"
    assert list(decision.score_breakdown.keys()) == ["NYSE"]


def test_unavailable_venues_are_excluded():
    router = _seed_router()
    router.update_snapshot(VenueSnapshot("XNAS", latency_ms=12, fill_rate=0.92, fee_bps=0.8, available=False))
    req = RouteRequest(symbol="MSFT", side="buy", qty=10, allowed_venues=["XNAS"])
    decision = router.select_venue(req)
    assert decision.allowed is False
    assert decision.venue is None
    assert decision.reason == "No available venue candidates"


def test_router_prefers_fast_when_weights_latency_heavy():
    router = ExchangeRouter(latency_weight=0.8, fill_rate_weight=0.1, cost_weight=0.1)
    router.update_snapshot(VenueSnapshot("FAST", latency_ms=2, fill_rate=0.8, fee_bps=1.5))
    router.update_snapshot(VenueSnapshot("SLOW", latency_ms=30, fill_rate=0.99, fee_bps=0.2))
    decision = router.select_venue(RouteRequest(symbol="NVDA", side="buy", qty=50))
    assert decision.venue == "FAST"


def test_router_prefers_fill_when_fillrate_heavy():
    router = ExchangeRouter(latency_weight=0.1, fill_rate_weight=0.8, cost_weight=0.1)
    router.update_snapshot(VenueSnapshot("FAST", latency_ms=2, fill_rate=0.7, fee_bps=0.2))
    router.update_snapshot(VenueSnapshot("DEEP", latency_ms=10, fill_rate=0.99, fee_bps=0.2))
    decision = router.select_venue(RouteRequest(symbol="NVDA", side="sell", qty=50))
    assert decision.venue == "DEEP"


def test_tie_break_is_deterministic_by_venue_name():
    router = ExchangeRouter()
    router.update_snapshot(VenueSnapshot("AAA", latency_ms=10, fill_rate=0.9, fee_bps=1.0))
    router.update_snapshot(VenueSnapshot("BBB", latency_ms=10, fill_rate=0.9, fee_bps=1.0))
    d1 = router.select_venue(RouteRequest(symbol="SPY", side="buy", qty=1))
    d2 = router.select_venue(RouteRequest(symbol="SPY", side="buy", qty=1))
    assert d1.venue == "AAA"
    assert d2.venue == "AAA"


def test_snapshot_all_and_get_snapshot():
    router = _seed_router()
    all_data = router.snapshot_all()
    assert set(all_data.keys()) == {"BATS", "NYSE", "XNAS"}
    one = router.get_snapshot("XNAS")
    assert one is not None
    assert one.latency_ms == 12


def test_no_snapshots_returns_no_route():
    router = ExchangeRouter()
    decision = router.select_venue(RouteRequest(symbol="SPY", side="buy", qty=100))
    assert decision.allowed is False
    assert decision.venue is None


def test_all_venues_unavailable_returns_no_route():
    router = ExchangeRouter()
    router.update_snapshot(VenueSnapshot("A", latency_ms=5, fill_rate=0.9, fee_bps=1.0, available=False))
    router.update_snapshot(VenueSnapshot("B", latency_ms=5, fill_rate=0.9, fee_bps=1.0, available=False))
    decision = router.select_venue(RouteRequest(symbol="SPY", side="buy", qty=100))
    assert decision.allowed is False
    assert decision.venue is None


def test_single_venue_always_selected():
    router = ExchangeRouter()
    router.update_snapshot(VenueSnapshot("ONLY", latency_ms=10, fill_rate=0.95, fee_bps=0.5))
    decision = router.select_venue(RouteRequest(symbol="AAPL", side="buy", qty=50))
    assert decision.allowed is True
    assert decision.venue == "ONLY"
    assert decision.score_breakdown["ONLY"]["total"] > 0


def test_allowed_venues_filter_excludes_others():
    router = _seed_router()
    decision = router.select_venue(
        RouteRequest(symbol="AAPL", side="buy", qty=100, allowed_venues=["BATS", "NYSE"])
    )
    assert decision.venue in {"BATS", "NYSE"}
    assert "XNAS" not in decision.score_breakdown


def test_allowed_venues_no_match_returns_no_route():
    router = _seed_router()
    decision = router.select_venue(
        RouteRequest(symbol="AAPL", side="buy", qty=100, allowed_venues=["UNKNOWN"])
    )
    assert decision.allowed is False
    assert decision.venue is None


def test_update_snapshot_replaces_existing():
    router = ExchangeRouter()
    router.update_snapshot(VenueSnapshot("V1", latency_ms=100, fill_rate=0.5, fee_bps=5.0))
    router.update_snapshot(VenueSnapshot("V1", latency_ms=1, fill_rate=0.99, fee_bps=0.1))
    snap = router.get_snapshot("V1")
    assert snap.latency_ms == 1
    assert snap.fill_rate == 0.99


def test_get_snapshot_nonexistent_returns_none():
    router = ExchangeRouter()
    assert router.get_snapshot("NOPE") is None


def test_cost_weight_dominates():
    router = ExchangeRouter(latency_weight=0.1, fill_rate_weight=0.1, cost_weight=0.8)
    router.update_snapshot(VenueSnapshot("CHEAP", latency_ms=50, fill_rate=0.8, fee_bps=0.1, slippage_bps=0.0))
    router.update_snapshot(VenueSnapshot("FAST", latency_ms=1, fill_rate=0.99, fee_bps=5.0, slippage_bps=2.0))
    decision = router.select_venue(RouteRequest(symbol="X", side="buy", qty=10))
    assert decision.venue == "CHEAP"


def test_slippage_included_in_cost():
    router = ExchangeRouter(latency_weight=0.0, fill_rate_weight=0.0, cost_weight=1.0)
    router.update_snapshot(VenueSnapshot("LOW_FEE", latency_ms=10, fill_rate=0.9, fee_bps=0.1, slippage_bps=5.0))
    router.update_snapshot(VenueSnapshot("HIGH_FEE", latency_ms=10, fill_rate=0.9, fee_bps=3.0, slippage_bps=0.0))
    decision = router.select_venue(RouteRequest(symbol="X", side="buy", qty=10))
    assert decision.venue == "HIGH_FEE"


def test_score_breakdown_contains_all_candidates():
    router = _seed_router()
    decision = router.select_venue(RouteRequest(symbol="AAPL", side="buy", qty=100))
    assert set(decision.score_breakdown.keys()) == {"BATS", "NYSE", "XNAS"}
    for venue_scores in decision.score_breakdown.values():
        assert "latency" in venue_scores
        assert "fill_rate" in venue_scores
        assert "cost" in venue_scores
        assert "total" in venue_scores


def test_deterministic_across_repeated_calls():
    router = _seed_router()
    req = RouteRequest(symbol="SPY", side="buy", qty=100)
    results = [router.select_venue(req).venue for _ in range(10)]
    assert len(set(results)) == 1


def test_snapshot_all_returns_sorted_keys():
    router = ExchangeRouter()
    router.update_snapshot(VenueSnapshot("ZZZ", latency_ms=1, fill_rate=0.9, fee_bps=0.1))
    router.update_snapshot(VenueSnapshot("AAA", latency_ms=1, fill_rate=0.9, fee_bps=0.1))
    router.update_snapshot(VenueSnapshot("MMM", latency_ms=1, fill_rate=0.9, fee_bps=0.1))
    keys = list(router.snapshot_all().keys())
    assert keys == ["AAA", "MMM", "ZZZ"]


def test_venue_snapshot_auto_timestamp():
    snap = VenueSnapshot("V", latency_ms=1, fill_rate=0.9, fee_bps=0.5)
    assert snap.timestamp != ""
    assert "T" in snap.timestamp


def test_venue_snapshot_explicit_timestamp():
    snap = VenueSnapshot("V", latency_ms=1, fill_rate=0.9, fee_bps=0.5, timestamp="2025-01-01T00:00:00Z")
    assert snap.timestamp == "2025-01-01T00:00:00Z"


def test_route_decision_fields():
    from execution.exchange_router import RouteDecision
    d = RouteDecision(venue="X", allowed=True, reason="ok", score_breakdown={"X": {"total": 1.0}})
    assert d.venue == "X"
    assert d.allowed is True
    assert d.score_breakdown["X"]["total"] == 1.0


def test_route_request_defaults():
    req = RouteRequest(symbol="AAPL", side="buy", qty=10)
    assert req.order_type == "market"
    assert req.allowed_venues == []


def test_mixed_available_and_unavailable_venues():
    router = ExchangeRouter()
    router.update_snapshot(VenueSnapshot("DOWN", latency_ms=1, fill_rate=0.99, fee_bps=0.1, available=False))
    router.update_snapshot(VenueSnapshot("UP", latency_ms=50, fill_rate=0.7, fee_bps=3.0, available=True))
    decision = router.select_venue(RouteRequest(symbol="X", side="buy", qty=10))
    assert decision.venue == "UP"
    assert decision.allowed is True
    assert "DOWN" not in decision.score_breakdown
