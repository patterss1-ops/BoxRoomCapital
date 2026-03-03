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
