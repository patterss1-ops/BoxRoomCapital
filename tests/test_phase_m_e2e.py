"""Phase M end-to-end acceptance tests (M-007).

Exercises all six implementation tickets:
  M-001  execution/algo_orders.py       TWAP/VWAP execution algorithms
  M-002  intelligence/feature_store.py  Feature store for ML signals
  M-003  risk/adaptive_sizer.py         Adaptive position sizing
  M-004  execution/exchange_router.py   Multi-exchange order router
  M-005  analytics/anomaly_detector.py  Anomaly detection
  M-006  risk/compliance_engine.py      Compliance rule engine
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

# M-001
from execution.algo_orders import (
    AlgoExecutionEngine,
    AlgoOrderConfig,
    AlgoType,
    SliceStatus,
)

# M-002
from intelligence.feature_store import FeatureRecord, FeatureStore

# M-003
from risk.adaptive_sizer import (
    AdaptivePositionSizer,
    SizingConfig,
    VolatilityMethod,
)

# M-004
from execution.exchange_router import (
    ExchangeRouter,
    RouteRequest,
    VenueSnapshot,
)

# M-005
from analytics.anomaly_detector import (
    AnomalyConfig,
    AnomalyDetector,
    AnomalyType,
)

# M-006
from risk.compliance_engine import (
    ComplianceEngine,
    ComplianceRuleConfig,
)


# ====================================================================
# Section 1 — Algo Orders E2E (M-001)
# ====================================================================


class TestAlgoOrdersE2E:
    """TWAP/VWAP execution algorithm end-to-end tests."""

    def test_twap_create_fill_all_slices_completed(self) -> None:
        """Create a TWAP order, fill every slice, assert COMPLETED."""
        engine = AlgoExecutionEngine()
        cfg = AlgoOrderConfig(
            algo_type=AlgoType.TWAP,
            ticker="AAPL",
            side="BUY",
            total_qty=300.0,
            duration_seconds=60,
            num_slices=3,
        )
        order = engine.create_order(cfg)

        assert order.status == "ACTIVE"
        assert len(order.slices) == 3

        # Each TWAP slice should be equally sized
        for sl in order.slices:
            assert sl.target_qty == pytest.approx(100.0)

        # Fill all slices
        for i in range(3):
            engine.fill_slice(order.order_id, i, 100.0, price=150.0)

        updated = engine.get_order(order.order_id)
        assert updated is not None
        assert updated.status == "COMPLETED"
        assert updated.total_filled == pytest.approx(300.0)
        assert updated.completion_pct == pytest.approx(100.0)
        assert updated.avg_fill_price == pytest.approx(150.0)

    def test_twap_partial_fill_stays_active(self) -> None:
        """A partially-filled TWAP order remains ACTIVE."""
        engine = AlgoExecutionEngine()
        cfg = AlgoOrderConfig(
            algo_type=AlgoType.TWAP,
            ticker="GOOG",
            side="SELL",
            total_qty=200.0,
            duration_seconds=120,
            num_slices=4,
        )
        order = engine.create_order(cfg)
        engine.fill_slice(order.order_id, 0, 50.0, price=2800.0)

        updated = engine.get_order(order.order_id)
        assert updated is not None
        assert updated.status == "ACTIVE"
        assert updated.completion_pct == pytest.approx(25.0)

    def test_vwap_distribution(self) -> None:
        """VWAP with 9 slices should follow 30/50/20 distribution."""
        engine = AlgoExecutionEngine()
        cfg = AlgoOrderConfig(
            algo_type=AlgoType.VWAP,
            ticker="MSFT",
            side="BUY",
            total_qty=900.0,
            duration_seconds=90,
            num_slices=9,
        )
        order = engine.create_order(cfg)

        assert len(order.slices) == 9

        # 9 slices -> 3 first, 3 mid, 3 last
        first_third = sum(s.target_qty for s in order.slices[:3])
        mid_third = sum(s.target_qty for s in order.slices[3:6])
        last_third = sum(s.target_qty for s in order.slices[6:9])

        assert first_third == pytest.approx(270.0)   # 30%
        assert mid_third == pytest.approx(450.0)      # 50%
        assert last_third == pytest.approx(180.0)     # 20%
        assert first_third + mid_third + last_third == pytest.approx(900.0)

    def test_twap_price_limit_rejection(self) -> None:
        """Fill above price limit raises ValueError and marks slice FAILED."""
        engine = AlgoExecutionEngine()
        cfg = AlgoOrderConfig(
            algo_type=AlgoType.TWAP,
            ticker="TSLA",
            side="BUY",
            total_qty=100.0,
            duration_seconds=30,
            num_slices=2,
            price_limit=200.0,
        )
        order = engine.create_order(cfg)

        with pytest.raises(ValueError, match="exceeds buy limit"):
            engine.fill_slice(order.order_id, 0, 50.0, price=250.0)

        assert order.slices[0].status == SliceStatus.FAILED

    def test_cancel_order(self) -> None:
        """Cancelling an order marks remaining pending slices CANCELLED."""
        engine = AlgoExecutionEngine()
        cfg = AlgoOrderConfig(
            algo_type=AlgoType.TWAP,
            ticker="AMZN",
            side="BUY",
            total_qty=400.0,
            duration_seconds=40,
            num_slices=4,
        )
        order = engine.create_order(cfg)
        engine.fill_slice(order.order_id, 0, 100.0, price=180.0)
        engine.cancel_order(order.order_id)

        assert order.status == "CANCELLED"
        assert order.slices[0].status == SliceStatus.FILLED
        for sl in order.slices[1:]:
            assert sl.status == SliceStatus.CANCELLED


# ====================================================================
# Section 2 — Feature Store E2E (M-002)
# ====================================================================


class TestFeatureStoreE2E:
    """Feature store persistence and retrieval tests."""

    def test_store_and_retrieve_features(self) -> None:
        """Save a feature record and retrieve it by id."""
        store = FeatureStore()
        rec = FeatureRecord(
            entity_id="AAPL",
            event_ts="2025-01-15T10:00:00+00:00",
            feature_set="momentum",
            feature_version=1,
            features={"rsi_14": 62.5, "macd": 1.23},
        )
        rid = store.save(rec)
        fetched = store.get(rid)

        assert fetched is not None
        assert fetched.entity_id == "AAPL"
        assert fetched.features["rsi_14"] == pytest.approx(62.5)
        assert fetched.features["macd"] == pytest.approx(1.23)
        assert fetched.feature_set == "momentum"

    def test_query_by_entity_and_feature_set(self) -> None:
        """Query returns only matching records."""
        store = FeatureStore()
        for i in range(5):
            store.save(FeatureRecord(
                entity_id="AAPL",
                event_ts=f"2025-01-{15 + i:02d}T10:00:00+00:00",
                feature_set="momentum",
                feature_version=1,
                features={"rsi_14": 50.0 + i},
            ))
        store.save(FeatureRecord(
            entity_id="GOOG",
            event_ts="2025-01-15T10:00:00+00:00",
            feature_set="momentum",
            feature_version=1,
            features={"rsi_14": 70.0},
        ))

        results = store.query(entity_id="AAPL", feature_set="momentum")
        assert len(results) == 5
        assert all(r.entity_id == "AAPL" for r in results)

    def test_point_in_time_retrieval(self) -> None:
        """Point-in-time query returns the latest record <= as_of_ts."""
        store = FeatureStore()
        store.save(FeatureRecord(
            entity_id="AAPL",
            event_ts="2025-01-10T10:00:00+00:00",
            feature_set="vol",
            feature_version=1,
            features={"iv_30": 0.25},
        ))
        store.save(FeatureRecord(
            entity_id="AAPL",
            event_ts="2025-01-20T10:00:00+00:00",
            feature_set="vol",
            feature_version=1,
            features={"iv_30": 0.30},
        ))

        pit = store.get_point_in_time(
            entity_id="AAPL",
            feature_set="vol",
            as_of_ts="2025-01-15T00:00:00+00:00",
        )
        assert pit is not None
        assert pit.features["iv_30"] == pytest.approx(0.25)

    def test_batch_save_and_count(self) -> None:
        """Batch save multiple records and verify count."""
        store = FeatureStore()
        records = [
            FeatureRecord(
                entity_id="ETH",
                event_ts=f"2025-02-{i + 1:02d}T00:00:00+00:00",
                feature_set="crypto",
                feature_version=1,
                features={"price": 3000.0 + i},
            )
            for i in range(10)
        ]
        ids = store.save_batch(records)
        assert len(ids) == 10
        assert store.count(entity_id="ETH") == 10

    def test_get_latest(self) -> None:
        """get_latest returns the most recent record by event_ts."""
        store = FeatureStore()
        store.save(FeatureRecord(
            entity_id="BTC",
            event_ts="2025-03-01T00:00:00+00:00",
            feature_set="crypto",
            feature_version=1,
            features={"price": 60000.0},
        ))
        store.save(FeatureRecord(
            entity_id="BTC",
            event_ts="2025-03-10T00:00:00+00:00",
            feature_set="crypto",
            feature_version=1,
            features={"price": 65000.0},
        ))
        latest = store.get_latest("BTC", "crypto")
        assert latest is not None
        assert latest.features["price"] == pytest.approx(65000.0)


# ====================================================================
# Section 3 — Adaptive Sizing E2E (M-003)
# ====================================================================


class TestAdaptiveSizingE2E:
    """Adaptive position sizing tests."""

    @staticmethod
    def _stable_prices(base: float = 100.0, count: int = 25) -> list[float]:
        """Generate a price series that alternates +1/-1 around base."""
        return [base + (1.0 if i % 2 == 0 else -1.0) for i in range(count)]

    def test_atr_based_sizing_with_known_data(self) -> None:
        """With known ATR, verify the calculated position size."""
        prices = self._stable_prices(100.0, 25)
        # ATR for this series: mean of |diffs| = constant 2.0
        cfg = SizingConfig(
            method=VolatilityMethod.ATR,
            lookback_period=20,
            risk_per_trade_pct=1.0,
            max_position_pct=10.0,
        )
        sizer = AdaptivePositionSizer(cfg)
        vol = sizer.compute_volatility(prices)
        assert vol == pytest.approx(2.0)

        result = sizer.calculate_size("TEST", prices, portfolio_value=100_000.0)
        # risk_amount = 100000 * 0.01 = 1000
        # raw_size = 1000 / 2.0 = 500
        # current_price = prices[-1] = 101.0 (index 24, even => 100+1)
        # max_shares = (100000 * 0.10) / 101.0 = 99.0099
        # capped then floored to 99
        assert result.volatility == pytest.approx(2.0)
        assert result.risk_amount == pytest.approx(1000.0)
        assert result.capped is True
        assert result.adjusted_size == pytest.approx(99.0)

    def test_higher_volatility_reduces_position_size(self) -> None:
        """Higher-volatility asset gets a smaller raw position size."""
        cfg = SizingConfig(
            method=VolatilityMethod.ATR,
            lookback_period=20,
            risk_per_trade_pct=1.0,
            max_position_pct=100.0,  # high cap to avoid capping effect
        )
        sizer = AdaptivePositionSizer(cfg)

        low_vol = [100.0 + (0.5 if i % 2 == 0 else -0.5) for i in range(25)]
        high_vol = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(25)]

        res_low = sizer.calculate_size("LOW_VOL", low_vol, portfolio_value=100_000.0)
        res_high = sizer.calculate_size("HIGH_VOL", high_vol, portfolio_value=100_000.0)

        assert res_low.raw_size > res_high.raw_size
        assert res_high.volatility > res_low.volatility

    def test_rolling_std_method(self) -> None:
        """Rolling std volatility method returns a non-zero value."""
        cfg = SizingConfig(method=VolatilityMethod.ROLLING_STD, lookback_period=10)
        sizer = AdaptivePositionSizer(cfg)
        prices = [100.0 + i * 0.5 for i in range(20)]
        vol = sizer.compute_volatility(prices)
        assert vol > 0.0

    def test_ewma_method(self) -> None:
        """EWMA volatility method returns a non-zero value."""
        cfg = SizingConfig(method=VolatilityMethod.EWMA, ewma_span=10)
        sizer = AdaptivePositionSizer(cfg)
        prices = [100.0 + i * 0.5 for i in range(20)]
        vol = sizer.compute_volatility(prices)
        assert vol > 0.0

    def test_batch_sizing(self) -> None:
        """calculate_batch returns results for all tickers."""
        sizer = AdaptivePositionSizer()
        tickers = ["A", "B", "C"]
        prices_map = {
            t: [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(25)]
            for t in tickers
        }
        results = sizer.calculate_batch(tickers, prices_map, portfolio_value=50_000.0)
        assert len(results) == 3
        assert all(r.adjusted_size > 0 for r in results)


# ====================================================================
# Section 4 — Exchange Router E2E (M-004)
# ====================================================================


class TestExchangeRouterE2E:
    """Multi-exchange order router tests."""

    @staticmethod
    def _build_router() -> ExchangeRouter:
        router = ExchangeRouter()
        router.update_snapshot(VenueSnapshot(
            venue="BINANCE", latency_ms=5.0, fill_rate=0.95, fee_bps=10.0,
        ))
        router.update_snapshot(VenueSnapshot(
            venue="COINBASE", latency_ms=20.0, fill_rate=0.90, fee_bps=15.0,
        ))
        router.update_snapshot(VenueSnapshot(
            venue="KRAKEN", latency_ms=30.0, fill_rate=0.85, fee_bps=12.0,
        ))
        return router

    def test_route_order_to_best_venue(self) -> None:
        """Router selects the best venue based on weighted score."""
        router = self._build_router()
        req = RouteRequest(symbol="BTC", side="BUY", qty=1.0)
        decision = router.select_venue(req)

        assert decision.allowed is True
        assert decision.venue is not None
        assert decision.reason == "ok"
        assert len(decision.score_breakdown) == 3

    def test_venue_selection_prefers_low_latency(self) -> None:
        """With heavy latency weight, lowest-latency venue wins."""
        router = ExchangeRouter(latency_weight=0.9, fill_rate_weight=0.05, cost_weight=0.05)
        router.update_snapshot(VenueSnapshot(
            venue="FAST", latency_ms=1.0, fill_rate=0.80, fee_bps=20.0,
        ))
        router.update_snapshot(VenueSnapshot(
            venue="SLOW", latency_ms=100.0, fill_rate=0.99, fee_bps=5.0,
        ))
        decision = router.select_venue(RouteRequest(symbol="ETH", side="SELL", qty=10.0))
        assert decision.venue == "FAST"

    def test_allowed_venues_filter(self) -> None:
        """Only allowed venues are considered."""
        router = self._build_router()
        req = RouteRequest(
            symbol="ETH", side="BUY", qty=5.0,
            allowed_venues=["COINBASE", "KRAKEN"],
        )
        decision = router.select_venue(req)
        assert decision.venue in ("COINBASE", "KRAKEN")

    def test_no_available_venue(self) -> None:
        """No available venue returns allowed=False."""
        router = ExchangeRouter()
        router.update_snapshot(VenueSnapshot(
            venue="DOWN", latency_ms=10.0, fill_rate=0.5, fee_bps=10.0, available=False,
        ))
        decision = router.select_venue(RouteRequest(symbol="X", side="BUY", qty=1.0))
        assert decision.allowed is False
        assert decision.venue is None

    def test_snapshot_all(self) -> None:
        """snapshot_all returns metrics for every registered venue."""
        router = self._build_router()
        snaps = router.snapshot_all()
        assert set(snaps.keys()) == {"BINANCE", "COINBASE", "KRAKEN"}
        assert all("latency_ms" in v for v in snaps.values())


# ====================================================================
# Section 5 — Anomaly Detection E2E (M-005)
# ====================================================================


class TestAnomalyDetectionE2E:
    """Statistical anomaly detection tests."""

    @staticmethod
    def _stable_with_spike(
        length: int = 50, spike_idx: int = 40, spike_return: float = 0.5,
    ) -> list[float]:
        """Generate stable prices with a large spike at spike_idx."""
        prices = [100.0 + 0.1 * i for i in range(length)]
        prices[spike_idx] = prices[spike_idx - 1] * (1 + spike_return)
        return prices

    def test_detect_price_spike_in_synthetic_data(self) -> None:
        """A 50 % price jump in otherwise trending data triggers a spike."""
        prices = self._stable_with_spike(50, spike_idx=40, spike_return=0.5)
        detector = AnomalyDetector(AnomalyConfig(
            z_score_threshold=2.5, lookback_window=30, min_data_points=10,
        ))
        anomalies = detector.detect_price_spike(prices, ticker="SPIKE_TEST")
        assert len(anomalies) >= 1
        assert all(a.anomaly_type == AnomalyType.PRICE_SPIKE for a in anomalies)
        assert any(a.ticker == "SPIKE_TEST" for a in anomalies)

    def test_no_anomaly_in_smooth_data(self) -> None:
        """A perfectly linear series produces no anomalies."""
        prices = [100.0 + 0.01 * i for i in range(60)]
        detector = AnomalyDetector(AnomalyConfig(
            z_score_threshold=2.5, lookback_window=30, min_data_points=10,
        ))
        anomalies = detector.detect_price_spike(prices, ticker="SMOOTH")
        assert len(anomalies) == 0

    def test_scan_all_across_multiple_tickers(self) -> None:
        """scan_all detects anomalies across a dict of tickers."""
        stable = [100.0 + 0.1 * i for i in range(50)]
        spiked = list(stable)
        spiked[40] = spiked[39] * 2.0  # 100 % spike

        detector = AnomalyDetector(AnomalyConfig(
            z_score_threshold=2.0, lookback_window=30, min_data_points=10,
        ))
        data = {
            "CLEAN": {"prices": stable, "volumes": stable},
            "DIRTY": {"prices": spiked, "volumes": stable},
        }
        anomalies = detector.scan_all(data)
        dirty_anomalies = [a for a in anomalies if a.ticker == "DIRTY"]
        assert len(dirty_anomalies) >= 1

    def test_severity_classification(self) -> None:
        """classify_severity maps z-scores to the documented buckets."""
        detector = AnomalyDetector()
        assert detector.classify_severity(2.0).value == "low"
        assert detector.classify_severity(2.7).value == "medium"
        assert detector.classify_severity(3.5).value == "high"
        assert detector.classify_severity(5.0).value == "critical"

    def test_detect_volume_surge(self) -> None:
        """Large volume outlier triggers VOLUME_SURGE anomaly."""
        # Need slight variation so std != 0 in the lookback window
        import random
        rng = random.Random(42)
        volumes = [1000.0 + rng.uniform(-10, 10) for _ in range(40)]
        volumes.append(50_000.0)  # massive surge at end
        detector = AnomalyDetector(AnomalyConfig(
            z_score_threshold=2.5, lookback_window=30, min_data_points=10,
        ))
        anomalies = detector.detect_volume_surge(volumes, ticker="VOL_TEST")
        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == AnomalyType.VOLUME_SURGE


# ====================================================================
# Section 6 — Compliance Engine E2E (M-006)
# ====================================================================


class TestComplianceEngineE2E:
    """Pre/post-trade compliance and breach reporting tests."""

    def test_pre_trade_allowed(self) -> None:
        """A normal order passes pre-trade compliance."""
        engine = ComplianceEngine(ComplianceRuleConfig(
            max_order_notional=200_000.0,
        ))
        decision = engine.evaluate_pre_trade(
            {"symbol": "AAPL", "qty": 100, "price": 150.0},
        )
        assert decision.allowed is True
        assert decision.phase == "pre_trade"
        assert len(decision.violations) == 0

    def test_pre_trade_blocked_symbol(self) -> None:
        """Blocked symbol triggers SYMBOL_BLOCKED violation."""
        engine = ComplianceEngine(ComplianceRuleConfig(
            blocked_symbols={"SCAM"},
        ))
        decision = engine.evaluate_pre_trade(
            {"symbol": "SCAM", "qty": 10, "price": 5.0},
        )
        assert decision.allowed is False
        codes = [v.code for v in decision.violations]
        assert "SYMBOL_BLOCKED" in codes

    def test_pre_trade_max_notional_exceeded(self) -> None:
        """Order notional exceeding limit is rejected."""
        engine = ComplianceEngine(ComplianceRuleConfig(
            max_order_notional=10_000.0,
        ))
        decision = engine.evaluate_pre_trade(
            {"symbol": "AAPL", "qty": 1000, "price": 150.0},
        )
        assert decision.allowed is False
        codes = [v.code for v in decision.violations]
        assert "MAX_ORDER_NOTIONAL_EXCEEDED" in codes

    def test_breach_report(self) -> None:
        """breach_report returns all denied decisions."""
        engine = ComplianceEngine(ComplianceRuleConfig(
            max_order_notional=1_000.0,
        ))
        # One allowed
        engine.evaluate_pre_trade({"symbol": "X", "qty": 1, "price": 10.0})
        # One denied
        engine.evaluate_pre_trade({"symbol": "X", "qty": 100, "price": 100.0})

        report = engine.breach_report()
        assert len(report) == 1
        assert "MAX_ORDER_NOTIONAL_EXCEEDED" in report[0]["violation_codes"]

    def test_post_trade_wash_trade_warning(self) -> None:
        """Opposite-side fill within cooldown triggers WASH_TRADE_RISK."""
        engine = ComplianceEngine(ComplianceRuleConfig(
            wash_trade_cooldown_seconds=300,
        ))
        now = datetime.now(timezone.utc)
        prev_fill = {
            "symbol": "BTC",
            "side": "BUY",
            "fill_ts": (now - timedelta(seconds=60)).isoformat(),
        }
        decision = engine.evaluate_post_trade(
            {"symbol": "BTC", "side": "SELL", "fill_ts": now.isoformat()},
            context={"recent_fills": [prev_fill]},
        )
        # wash_trade_risk has severity="warning" so allowed is True
        assert decision.allowed is True
        codes = [v.code for v in decision.violations]
        assert "WASH_TRADE_RISK" in codes


# ====================================================================
# Section 7 — Cross-Module Integration
# ====================================================================


class TestCrossModuleIntegration:
    """Tests combining multiple Phase M modules end-to-end."""

    def test_adaptive_sizer_feeds_algo_order(self) -> None:
        """Adaptive sizer computes quantity, then TWAP executes it."""
        # -- sizing --
        prices = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(25)]
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            lookback_period=20,
            risk_per_trade_pct=2.0,
            max_position_pct=50.0,
        ))
        sizing = sizer.calculate_size("INTG", prices, portfolio_value=200_000.0)
        qty = sizing.adjusted_size
        assert qty > 0

        # -- algo execution --
        engine = AlgoExecutionEngine()
        cfg = AlgoOrderConfig(
            algo_type=AlgoType.TWAP,
            ticker="INTG",
            side="BUY",
            total_qty=qty,
            duration_seconds=60,
            num_slices=4,
        )
        order = engine.create_order(cfg)
        slice_qty = qty / 4.0
        for i in range(4):
            engine.fill_slice(order.order_id, i, slice_qty, price=100.0)

        updated = engine.get_order(order.order_id)
        assert updated is not None
        assert updated.status == "COMPLETED"
        assert updated.total_filled == pytest.approx(qty, abs=1.0)

    def test_anomaly_detector_monitors_execution_drift(self) -> None:
        """Anomaly detector catches execution drift in fill prices."""
        # Simulate fill prices with slight jitter so std > 0, plus a big outlier
        import random
        rng = random.Random(99)
        fill_prices = [150.0 + rng.uniform(-0.5, 0.5) for _ in range(40)]
        fill_prices.append(300.0)  # big outlier at end
        detector = AnomalyDetector(AnomalyConfig(
            z_score_threshold=2.5, lookback_window=30, min_data_points=10,
        ))
        anomalies = detector.detect_zscore(
            fill_prices, ticker="EXEC", anomaly_type=AnomalyType.EXECUTION_DRIFT,
        )
        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == AnomalyType.EXECUTION_DRIFT

    def test_full_pipeline_size_route_execute_detect_comply(self) -> None:
        """Full pipeline: size -> route -> execute -> detect anomalies -> compliance."""
        # 1. Size position
        prices = [50.0 + (0.5 if i % 2 == 0 else -0.5) for i in range(25)]
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            lookback_period=20,
            risk_per_trade_pct=1.0,
            max_position_pct=20.0,
        ))
        sizing = sizer.calculate_size("PIPE", prices, portfolio_value=100_000.0)
        qty = sizing.adjusted_size
        assert qty > 0

        # 2. Route to exchange
        router = ExchangeRouter()
        router.update_snapshot(VenueSnapshot(
            venue="PRIMARY", latency_ms=2.0, fill_rate=0.98, fee_bps=8.0,
        ))
        router.update_snapshot(VenueSnapshot(
            venue="SECONDARY", latency_ms=15.0, fill_rate=0.90, fee_bps=12.0,
        ))
        route = router.select_venue(RouteRequest(symbol="PIPE", side="BUY", qty=qty))
        assert route.allowed is True
        chosen_venue = route.venue

        # 3. Execute via TWAP
        engine = AlgoExecutionEngine()
        order = engine.create_order(AlgoOrderConfig(
            algo_type=AlgoType.TWAP,
            ticker="PIPE",
            side="BUY",
            total_qty=qty,
            duration_seconds=30,
            num_slices=3,
        ))
        fill_price = 50.0
        fill_prices_for_detection = []
        for i in range(3):
            s = order.slices[i]
            engine.fill_slice(order.order_id, i, s.target_qty, price=fill_price)
            fill_prices_for_detection.append(fill_price)

        updated = engine.get_order(order.order_id)
        assert updated is not None
        assert updated.status == "COMPLETED"

        # 4. Anomaly detection on fills (all same price, no anomaly expected)
        detector = AnomalyDetector(AnomalyConfig(min_data_points=2))
        fill_series = [50.0] * 20 + fill_prices_for_detection
        anomalies = detector.detect_zscore(
            fill_series, ticker="PIPE", anomaly_type=AnomalyType.EXECUTION_DRIFT,
        )
        assert len(anomalies) == 0  # stable fills, no anomaly

        # 5. Compliance check
        compliance = ComplianceEngine(ComplianceRuleConfig(
            max_order_notional=500_000.0,
        ))
        decision = compliance.evaluate_pre_trade({
            "symbol": "PIPE",
            "qty": qty,
            "price": fill_price,
        })
        assert decision.allowed is True
        assert len(decision.violations) == 0

    def test_compliance_blocks_oversized_algo_order(self) -> None:
        """Compliance rejects an order when notional exceeds limits."""
        # Sizer suggests a size
        prices = [200.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(25)]
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            risk_per_trade_pct=5.0,
            max_position_pct=50.0,
        ))
        sizing = sizer.calculate_size("BIG", prices, portfolio_value=1_000_000.0)
        qty = sizing.adjusted_size

        # Compliance with tight limit rejects it
        compliance = ComplianceEngine(ComplianceRuleConfig(
            max_order_notional=5_000.0,
        ))
        decision = compliance.evaluate_pre_trade({
            "symbol": "BIG",
            "qty": qty,
            "price": 200.0,
        })
        assert decision.allowed is False
        assert any(v.code == "MAX_ORDER_NOTIONAL_EXCEEDED" for v in decision.violations)
