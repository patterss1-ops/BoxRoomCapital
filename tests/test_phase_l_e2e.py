"""Phase L end-to-end acceptance harness (L-007).

Exercises all six implementation tickets (L-001 through L-006) with
integration tests that validate each module individually and together
in cross-module workflows.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

import pytest

# L-001
from data.pipeline_orchestrator import (
    NodeConfig,
    NodeResult,
    NodeStatus,
    PipelineOrchestrator,
    PipelineResult,
    PipelineStatus,
)

# L-002
from data.market_calendar import MarketCalendar, SessionWindow

# L-003
from data.signal_store import SignalSnapshot, SignalStore

# L-004
from analytics.correlation_monitor import (
    CorrelationMonitor,
    CorrelationSnapshot,
    RegimeEvent,
)

# L-005
from app.notification_templates import (
    NotificationChannel,
    NotificationSeverity,
    NotificationTemplateEngine,
    RenderedNotification,
)

# L-006
from ops.metrics_collector import MetricPoint, SystemMetricsCollector


# ===================================================================
# Section 1: Pipeline Orchestrator E2E (L-001)
# ===================================================================


class TestPipelineOrchestratorE2E:
    """End-to-end tests for the DAG pipeline orchestrator."""

    def test_multi_node_pipeline_data_flow(self) -> None:
        """Multi-node pipeline with real data flow between nodes."""
        results: dict[str, str] = {}

        def step_fetch():
            results["fetch"] = "raw_data"

        def step_validate():
            assert results["fetch"] == "raw_data"
            results["validate"] = "validated"

        def step_transform():
            assert results["validate"] == "validated"
            results["transform"] = "transformed"

        def step_publish():
            assert results["transform"] == "transformed"
            results["publish"] = "published"

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="fetch", fn=step_fetch))
        orch.add_node(NodeConfig(name="validate", fn=step_validate, dependencies=["fetch"]))
        orch.add_node(NodeConfig(name="transform", fn=step_transform, dependencies=["validate"]))
        orch.add_node(NodeConfig(name="publish", fn=step_publish, dependencies=["transform"]))

        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        assert len(result.node_results) == 4
        for nr in result.node_results.values():
            assert nr.status == NodeStatus.SUCCESS
        assert results == {
            "fetch": "raw_data",
            "validate": "validated",
            "transform": "transformed",
            "publish": "published",
        }
        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.duration >= 0

    def test_failing_node_skip_propagation(self) -> None:
        """A failing node causes all downstream dependents to be skipped."""
        executed: list[str] = []

        def step_a():
            executed.append("a")

        def step_b_fails():
            executed.append("b")
            raise RuntimeError("b broke")

        def step_c():
            executed.append("c")

        def step_d():
            executed.append("d")

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="a", fn=step_a))
        orch.add_node(NodeConfig(name="b", fn=step_b_fails, dependencies=["a"]))
        orch.add_node(NodeConfig(name="c", fn=step_c, dependencies=["b"]))
        orch.add_node(NodeConfig(name="d", fn=step_d, dependencies=["c"]))

        result = orch.run()

        assert result.status == PipelineStatus.PARTIAL
        assert result.node_results["a"].status == NodeStatus.SUCCESS
        assert result.node_results["b"].status == NodeStatus.FAILED
        assert result.node_results["b"].error == "b broke"
        assert result.node_results["c"].status == NodeStatus.SKIPPED
        assert result.node_results["d"].status == NodeStatus.SKIPPED
        assert "a" in executed
        assert "b" in executed
        assert "c" not in executed
        assert "d" not in executed

    def test_parallel_branches_partial_failure(self) -> None:
        """Diamond DAG: one branch fails, sibling branch still executes."""
        executed: list[str] = []

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="root", fn=lambda: executed.append("root")))
        orch.add_node(NodeConfig(
            name="left_fail",
            fn=lambda: (_ for _ in ()).throw(ValueError("left broke")),
            dependencies=["root"],
        ))
        orch.add_node(NodeConfig(
            name="right_ok",
            fn=lambda: executed.append("right_ok"),
            dependencies=["root"],
        ))
        orch.add_node(NodeConfig(
            name="merge",
            fn=lambda: executed.append("merge"),
            dependencies=["left_fail", "right_ok"],
        ))

        result = orch.run()
        assert result.status == PipelineStatus.PARTIAL
        assert result.node_results["root"].status == NodeStatus.SUCCESS
        assert result.node_results["left_fail"].status == NodeStatus.FAILED
        assert result.node_results["right_ok"].status == NodeStatus.SUCCESS
        assert result.node_results["merge"].status == NodeStatus.SKIPPED

    def test_validation_detects_cycle(self) -> None:
        """Cycle detection in the pipeline DAG."""
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="x", fn=lambda: None, dependencies=["y"]))
        orch.add_node(NodeConfig(name="y", fn=lambda: None, dependencies=["x"]))

        errors = orch.validate()
        assert len(errors) > 0
        assert "cycle" in errors[0].lower()

    def test_retry_logic(self) -> None:
        """Node retries on failure before ultimately succeeding."""
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("transient")

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="flaky", fn=flaky, max_retries=2, retry_delay=0.0))
        result = orch.run()
        assert result.status == PipelineStatus.COMPLETED
        assert result.node_results["flaky"].status == NodeStatus.SUCCESS
        assert call_count["n"] == 3


# ===================================================================
# Section 2: Market Calendar E2E (L-002)
# ===================================================================


class TestMarketCalendarE2E:
    """End-to-end tests for the market calendar."""

    def test_trading_day_identification(self) -> None:
        """Calendar correctly identifies weekday trading days and weekends."""
        cal = MarketCalendar()

        # Monday 2026-01-05 is a regular trading day
        assert cal.is_trading_day(date(2026, 1, 5)) is True

        # Saturday & Sunday are not trading days
        assert cal.is_trading_day(date(2026, 1, 3)) is False  # Saturday
        assert cal.is_trading_day(date(2026, 1, 4)) is False  # Sunday

    def test_holiday_detection(self) -> None:
        """Known holidays are correctly flagged as non-trading."""
        cal = MarketCalendar()

        # Christmas 2025 is Thursday Dec 25
        assert cal.is_trading_day(date(2025, 12, 25)) is False
        assert cal.get_holiday_name(date(2025, 12, 25)) == "Christmas Day"

        # Independence Day 2026 is a Saturday => observed on Friday July 3
        assert cal.is_trading_day(date(2026, 7, 3)) is False
        assert cal.get_holiday_name(date(2026, 7, 3)) == "Independence Day"

    def test_session_window_regular(self) -> None:
        """Session window returns correct open/close for a regular day."""
        cal = MarketCalendar()
        # 2026-01-05 is a Monday (regular day)
        session = cal.get_session_window(date(2026, 1, 5))
        assert session is not None
        assert session.session_date == date(2026, 1, 5)
        assert session.is_early_close is False
        # open should be 9:30 ET => 14:30 UTC (EST offset -5h)
        assert session.open_utc.hour == 14
        assert session.open_utc.minute == 30

    def test_session_window_none_for_weekend(self) -> None:
        """No session window for weekends."""
        cal = MarketCalendar()
        assert cal.get_session_window(date(2026, 1, 3)) is None

    def test_next_and_previous_trading_day(self) -> None:
        """Navigation across weekends and holidays."""
        cal = MarketCalendar()
        # Friday 2026-01-02 -> next trading day should be Monday 2026-01-05
        assert cal.next_trading_day(date(2026, 1, 2)) == date(2026, 1, 5)
        # Monday 2026-01-05 -> previous trading day should be Friday 2026-01-02
        assert cal.previous_trading_day(date(2026, 1, 5)) == date(2026, 1, 2)

    def test_market_phase(self) -> None:
        """Market phase returns correct values for different times."""
        cal = MarketCalendar()
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")

        day = date(2026, 1, 5)  # Monday, regular trading day
        # 10:00 ET => regular session
        regular_time = datetime.combine(day, time(10, 0), tzinfo=ET).astimezone(timezone.utc)
        assert cal.market_phase(regular_time) == "regular"

        # 6:00 ET => pre_market
        pre_time = datetime.combine(day, time(6, 0), tzinfo=ET).astimezone(timezone.utc)
        assert cal.market_phase(pre_time) == "pre_market"

        # 17:00 ET => after_hours
        after_time = datetime.combine(day, time(17, 0), tzinfo=ET).astimezone(timezone.utc)
        assert cal.market_phase(after_time) == "after_hours"

        # Weekend => closed
        sat = date(2026, 1, 3)
        sat_time = datetime.combine(sat, time(12, 0), tzinfo=ET).astimezone(timezone.utc)
        assert cal.market_phase(sat_time) == "closed"


# ===================================================================
# Section 3: Signal Store E2E (L-003)
# ===================================================================


class TestSignalStoreE2E:
    """End-to-end tests for signal persistence and replay."""

    def _make_snapshot(
        self, ticker: str, score: float, verdict: str, scored_at: str | None = None
    ) -> SignalSnapshot:
        return SignalSnapshot(
            ticker=ticker,
            composite_score=score,
            layer_scores={"momentum": score * 0.6, "value": score * 0.4},
            verdict=verdict,
            confidence=0.85,
            scored_at=scored_at or datetime.now(timezone.utc).isoformat(),
        )

    def test_save_query_replay_workflow(self) -> None:
        """Save snapshots, query them, then replay in chronological order."""
        store = SignalStore()

        t1 = "2026-03-01T10:00:00+00:00"
        t2 = "2026-03-01T11:00:00+00:00"
        t3 = "2026-03-01T12:00:00+00:00"

        snap1 = self._make_snapshot("AAPL", 0.8, "BUY", scored_at=t1)
        snap2 = self._make_snapshot("AAPL", 0.3, "SELL", scored_at=t2)
        snap3 = self._make_snapshot("AAPL", 0.6, "HOLD", scored_at=t3)

        id1 = store.save(snap1)
        id2 = store.save(snap2)
        id3 = store.save(snap3)

        # Query all AAPL
        results = store.query(ticker="AAPL")
        assert len(results) == 3
        # Newest first
        assert results[0].scored_at == t3
        assert results[2].scored_at == t1

        # Get by ID
        fetched = store.get(id2)
        assert fetched is not None
        assert fetched.ticker == "AAPL"
        assert fetched.verdict == "SELL"

        # Replay in chronological order
        replayed = store.replay("AAPL", t1, t3)
        assert len(replayed) == 3
        assert replayed[0].scored_at == t1  # oldest first
        assert replayed[2].scored_at == t3

    def test_batch_save_and_ticker_history(self) -> None:
        """Batch save multiple snapshots and retrieve ticker history."""
        store = SignalStore()

        now = datetime.now(timezone.utc)
        snapshots = []
        for i in range(5):
            ts = (now - timedelta(days=i)).isoformat()
            snapshots.append(self._make_snapshot("MSFT", 0.5 + i * 0.05, "BUY", scored_at=ts))

        ids = store.save_batch(snapshots)
        assert len(ids) == 5

        # All snapshots are within 30 days
        history = store.get_ticker_history("MSFT", days=30)
        assert len(history) == 5

        # Latest should be the most recent
        latest = store.get_latest("MSFT")
        assert latest is not None
        assert latest.scored_at == snapshots[0].scored_at

        # Count
        assert store.count(ticker="MSFT") == 5
        assert store.count(ticker="GOOG") == 0

    def test_query_by_verdict(self) -> None:
        """Query filtering by verdict."""
        store = SignalStore()
        store.save(self._make_snapshot("AAPL", 0.9, "BUY"))
        store.save(self._make_snapshot("AAPL", 0.2, "SELL"))
        store.save(self._make_snapshot("GOOG", 0.8, "BUY"))

        buys = store.query(verdict="BUY")
        assert len(buys) == 2
        assert all(s.verdict == "BUY" for s in buys)

    def test_delete_before(self) -> None:
        """Delete old records and confirm they are gone."""
        store = SignalStore()
        store.save(self._make_snapshot("AAPL", 0.5, "HOLD", scored_at="2025-01-01T00:00:00+00:00"))
        store.save(self._make_snapshot("AAPL", 0.6, "HOLD", scored_at="2026-03-01T00:00:00+00:00"))

        deleted = store.delete_before("2026-01-01T00:00:00+00:00")
        assert deleted == 1
        assert store.count() == 1


# ===================================================================
# Section 4: Correlation Monitor E2E (L-004)
# ===================================================================


class TestCorrelationMonitorE2E:
    """End-to-end tests for correlation computation and regime detection."""

    def test_correlation_computation_with_sample_data(self) -> None:
        """Compute correlation snapshot for perfectly correlated and anti-correlated series."""
        monitor = CorrelationMonitor(window=20, min_points=5)

        # Perfectly correlated
        base = [float(i) for i in range(20)]
        series_map = {
            "AAPL": base,
            "MSFT": [x * 2 + 1 for x in base],   # perfectly correlated
            "GOOG": [-x for x in base],            # perfectly anti-correlated
        }

        snapshot = monitor.compute_snapshot(series_map)
        assert isinstance(snapshot, CorrelationSnapshot)
        assert set(snapshot.labels) == {"AAPL", "GOOG", "MSFT"}

        # AAPL-MSFT should be ~1.0
        assert snapshot.matrix["AAPL"]["MSFT"] == pytest.approx(1.0, abs=0.01)
        # AAPL-GOOG should be ~-1.0
        assert snapshot.matrix["AAPL"]["GOOG"] == pytest.approx(-1.0, abs=0.01)
        # Self-correlation = 1.0
        assert snapshot.matrix["AAPL"]["AAPL"] == 1.0

    def test_regime_shift_detection(self) -> None:
        """Detect a regime shift when correlations change dramatically."""
        monitor = CorrelationMonitor(
            window=20, min_points=5, regime_shift_threshold=0.35
        )

        base = [float(i) for i in range(20)]
        # Initial: correlated
        series_phase1 = {
            "SPY": base,
            "TLT": [x * 0.5 for x in base],
        }

        snap1, events1 = monitor.update(series_phase1)
        # First update: no previous snapshot, so no events
        assert len(events1) == 0
        assert snap1.matrix["SPY"]["TLT"] == pytest.approx(1.0, abs=0.01)

        # Phase 2: anti-correlated (big regime shift)
        series_phase2 = {
            "SPY": base,
            "TLT": [-x * 0.8 for x in base],
        }

        snap2, events2 = monitor.update(series_phase2)
        # Should detect a regime shift: from ~+1.0 to ~-1.0
        assert len(events2) >= 1
        regime_event = events2[0]
        assert regime_event.event_type == "regime_shift"
        assert regime_event.pair == ("SPY", "TLT")
        assert regime_event.previous_correlation == pytest.approx(1.0, abs=0.01)
        assert regime_event.current_correlation == pytest.approx(-1.0, abs=0.01)

    def test_decorrelation_detection(self) -> None:
        """Detect decorrelation when a highly correlated pair becomes uncorrelated."""
        monitor = CorrelationMonitor(
            window=30,
            min_points=5,
            regime_shift_threshold=2.0,  # disable regime_shift to isolate decorrelation
            high_correlation_threshold=0.75,
            low_correlation_threshold=0.20,
        )

        base = [float(i) for i in range(30)]
        # Phase 1: highly correlated
        series_a = {"X": base, "Y": [x * 1.5 for x in base]}
        snap1, _ = monitor.update(series_a)
        assert abs(snap1.matrix["X"]["Y"]) >= 0.75

        # Phase 2: essentially random / uncorrelated
        import random
        rng = random.Random(42)
        noise = [rng.gauss(0, 1) for _ in range(30)]
        series_b = {"X": base, "Y": noise}
        snap2, events2 = monitor.update(series_b)

        # Check that a decorrelation or regime shift event was detected
        assert len(events2) >= 1
        assert events2[0].event_type in ("decorrelation", "regime_shift")


# ===================================================================
# Section 5: Notification Templates E2E (L-005)
# ===================================================================


class TestNotificationTemplatesE2E:
    """End-to-end tests for notification template rendering."""

    def test_render_all_builtin_templates(self) -> None:
        """All built-in templates can be rendered successfully."""
        engine = NotificationTemplateEngine()

        template_vars = {
            "trade_executed": {"side": "BUY", "qty": 100, "ticker": "AAPL", "price": 150.25},
            "risk_alert": {"alert_type": "VaR Breach", "message": "Portfolio VaR exceeded 2%"},
            "drawdown_warning": {"strategy": "MeanReversion", "drawdown_pct": 5.2},
            "signal_generated": {"verdict": "BUY", "ticker": "GOOG", "score": 0.87},
            "system_health": {"component": "DataFeed", "status": "degraded"},
            "rebalance_triggered": {"strategy": "EqualWeight", "drift_pct": 3.5},
        }

        templates = engine.list_templates()
        assert len(templates) == 6
        for tpl_name in templates:
            assert tpl_name in template_vars, f"No test vars for {tpl_name}"
            rendered = engine.render(tpl_name, template_vars[tpl_name])
            # Each template has 3 channels by default
            assert len(rendered) == 3
            for r in rendered:
                assert r.template_name == tpl_name
                assert r.subject != ""
                assert r.body != ""
                assert r.rendered_at != ""

    def test_channel_specific_formatting_telegram(self) -> None:
        """Telegram channel wraps subject in bold markers."""
        engine = NotificationTemplateEngine()
        rendered = engine.render(
            "trade_executed",
            {"side": "BUY", "qty": 50, "ticker": "TSLA", "price": 200.0},
            channel=NotificationChannel.TELEGRAM,
        )
        assert len(rendered) == 1
        r = rendered[0]
        assert r.channel == NotificationChannel.TELEGRAM
        assert r.subject.startswith("*")
        assert r.subject.endswith("*")
        assert "TSLA" in r.subject

    def test_channel_specific_formatting_email(self) -> None:
        """Email channel wraps body in <p> tags."""
        engine = NotificationTemplateEngine()
        rendered = engine.render(
            "risk_alert",
            {"alert_type": "Margin Call", "message": "Margin below threshold"},
            channel=NotificationChannel.EMAIL,
        )
        assert len(rendered) == 1
        r = rendered[0]
        assert r.channel == NotificationChannel.EMAIL
        assert r.body.startswith("<p>")
        assert r.body.endswith("</p>")

    def test_channel_specific_formatting_log(self) -> None:
        """LOG channel prepends severity to body."""
        engine = NotificationTemplateEngine()
        rendered = engine.render(
            "system_health",
            {"component": "OrderRouter", "status": "healthy"},
            channel=NotificationChannel.LOG,
        )
        assert len(rendered) == 1
        r = rendered[0]
        assert r.channel == NotificationChannel.LOG
        assert "[WARNING]" in r.body

    def test_missing_variable_raises(self) -> None:
        """Missing required variables raises ValueError."""
        engine = NotificationTemplateEngine()
        with pytest.raises(ValueError, match="Missing required variables"):
            engine.render("trade_executed", {"side": "BUY"})

    def test_validate_returns_missing_vars(self) -> None:
        """Validate method returns list of missing required vars."""
        engine = NotificationTemplateEngine()
        missing = engine.validate("trade_executed", {"side": "BUY", "qty": 100})
        assert "ticker" in missing
        assert "price" in missing
        assert "side" not in missing


# ===================================================================
# Section 6: Metrics Collector E2E (L-006)
# ===================================================================


class TestMetricsCollectorE2E:
    """End-to-end tests for system metrics collection and aggregation."""

    def test_record_and_query(self) -> None:
        """Record metrics and retrieve them by name."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        mc.record("latency_ms", 42.0, at=now)
        mc.record("latency_ms", 55.0, at=now + timedelta(seconds=1))
        mc.record("throughput", 1000.0, at=now)

        latency_pts = mc.query("latency_ms", now=now + timedelta(seconds=2))
        assert len(latency_pts) == 2
        assert latency_pts[0].value == 42.0
        assert latency_pts[1].value == 55.0

    def test_aggregation_functions(self) -> None:
        """Test all aggregation modes: mean, sum, count, min, max, latest."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        for i, v in enumerate(values):
            mc.record("cpu", v, at=now + timedelta(seconds=i))

        ref = now + timedelta(seconds=10)
        assert mc.aggregate("cpu", agg="mean", window_seconds=300, now=ref) == pytest.approx(30.0)
        assert mc.aggregate("cpu", agg="sum", window_seconds=300, now=ref) == pytest.approx(150.0)
        assert mc.aggregate("cpu", agg="count", window_seconds=300, now=ref) == 5.0
        assert mc.aggregate("cpu", agg="min", window_seconds=300, now=ref) == 10.0
        assert mc.aggregate("cpu", agg="max", window_seconds=300, now=ref) == 50.0
        assert mc.aggregate("cpu", agg="latest", window_seconds=300, now=ref) == 50.0

    def test_snapshot_summary(self) -> None:
        """Snapshot returns per-metric aggregation dictionaries."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        mc.record("orders", 5.0, at=now)
        mc.record("orders", 10.0, at=now + timedelta(seconds=1))
        mc.record("errors", 1.0, at=now)

        snap = mc.snapshot(window_seconds=300, now=now + timedelta(seconds=5))
        assert "orders" in snap
        assert "errors" in snap
        assert snap["orders"]["count"] == 2.0
        assert snap["orders"]["sum"] == 15.0
        assert snap["errors"]["count"] == 1.0

    def test_tag_filtering(self) -> None:
        """Query by tags filters correctly."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        mc.record("latency", 10.0, tags={"endpoint": "/api/v1"}, at=now)
        mc.record("latency", 20.0, tags={"endpoint": "/api/v2"}, at=now)
        mc.record("latency", 30.0, tags={"endpoint": "/api/v1"}, at=now)

        v1_pts = mc.query("latency", tags={"endpoint": "/api/v1"}, now=now + timedelta(seconds=1))
        assert len(v1_pts) == 2
        assert all(p.tags["endpoint"] == "/api/v1" for p in v1_pts)

    def test_prometheus_output(self) -> None:
        """Prometheus rendering produces valid metric lines."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)
        mc.record("requests", 100.0, at=now)

        output = mc.render_prometheus(window_seconds=300, prefix="brc_test")
        assert "brc_test_requests_count" in output
        assert "brc_test_requests_sum" in output
        assert "# TYPE" in output

    def test_increment_helper(self) -> None:
        """Increment shorthand records a value of 1.0 by default."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        mc.increment("event_count", at=now)
        mc.increment("event_count", amount=2.0, at=now)

        pts = mc.query("event_count", now=now + timedelta(seconds=1))
        assert len(pts) == 2
        assert pts[0].value == 1.0
        assert pts[1].value == 2.0


# ===================================================================
# Section 7: Cross-Module Integration
# ===================================================================


class TestCrossModuleIntegration:
    """Cross-module integration tests that wire multiple L-subsystems together."""

    def test_pipeline_with_signal_store_and_notifications(self) -> None:
        """Pipeline orchestrator runs nodes that use signal store and notifications."""
        store = SignalStore()
        engine = NotificationTemplateEngine()
        notifications: list[RenderedNotification] = []

        def score_signals():
            snap = SignalSnapshot(
                ticker="NVDA",
                composite_score=0.92,
                layer_scores={"momentum": 0.95, "value": 0.88},
                verdict="BUY",
                confidence=0.90,
            )
            store.save(snap)

        def send_notification():
            latest = store.get_latest("NVDA")
            assert latest is not None
            rendered = engine.render(
                "signal_generated",
                {"verdict": latest.verdict, "ticker": latest.ticker, "score": latest.composite_score},
                channel=NotificationChannel.LOG,
            )
            notifications.extend(rendered)

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="score", fn=score_signals))
        orch.add_node(NodeConfig(name="notify", fn=send_notification, dependencies=["score"]))

        result = orch.run()
        assert result.status == PipelineStatus.COMPLETED
        assert store.count(ticker="NVDA") == 1
        assert len(notifications) == 1
        assert "NVDA" in notifications[0].body

    def test_pipeline_with_metrics_collection(self) -> None:
        """Pipeline nodes record metrics; verify aggregation after run."""
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        def step_fetch():
            mc.record("pipeline.fetch_latency_ms", 120.0, at=now)
            mc.increment("pipeline.fetch_count", at=now)

        def step_process():
            mc.record("pipeline.process_latency_ms", 350.0, at=now + timedelta(seconds=1))
            mc.increment("pipeline.process_count", at=now + timedelta(seconds=1))

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="fetch", fn=step_fetch))
        orch.add_node(NodeConfig(name="process", fn=step_process, dependencies=["fetch"]))
        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED

        ref = now + timedelta(seconds=10)
        assert mc.aggregate("pipeline.fetch_latency_ms", agg="latest", window_seconds=300, now=ref) == 120.0
        assert mc.aggregate("pipeline.process_latency_ms", agg="latest", window_seconds=300, now=ref) == 350.0
        snap = mc.snapshot(window_seconds=300, now=ref)
        assert "pipeline.fetch_count" in snap
        assert "pipeline.process_count" in snap

    def test_full_stack_pipeline(self) -> None:
        """Full-stack: calendar check -> correlation -> signal store -> notify -> metrics."""
        cal = MarketCalendar()
        monitor = CorrelationMonitor(window=20, min_points=5)
        store = SignalStore()
        engine = NotificationTemplateEngine()
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        context: dict = {}

        def check_calendar():
            # Use a known trading day
            day = date(2026, 1, 5)  # Monday
            context["is_trading_day"] = cal.is_trading_day(day)
            session = cal.get_session_window(day)
            context["session"] = session
            mc.increment("pipeline.calendar_check", at=now)

        def compute_correlations():
            assert context["is_trading_day"] is True
            base = [float(i) for i in range(20)]
            series = {
                "SPY": base,
                "QQQ": [x * 1.1 + 0.5 for x in base],
            }
            snap, events = monitor.update(series, at=now)
            context["corr_snapshot"] = snap
            mc.record("pipeline.correlation_spy_qqq", snap.matrix["QQQ"]["SPY"], at=now)

        def generate_signals():
            corr = context["corr_snapshot"]
            # Generate a signal based on the correlation
            signal = SignalSnapshot(
                ticker="SPY",
                composite_score=0.75,
                layer_scores={"correlation": corr.matrix["QQQ"]["SPY"], "trend": 0.8},
                verdict="BUY",
                confidence=0.85,
                metadata={"corr_window": corr.window},
            )
            store.save(signal)
            mc.increment("pipeline.signals_generated", at=now)

        def notify_results():
            latest = store.get_latest("SPY")
            assert latest is not None
            rendered = engine.render(
                "signal_generated",
                {"verdict": latest.verdict, "ticker": latest.ticker, "score": latest.composite_score},
                channel=NotificationChannel.TELEGRAM,
            )
            context["notifications"] = rendered
            mc.increment("pipeline.notifications_sent", at=now)

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="calendar", fn=check_calendar))
        orch.add_node(NodeConfig(name="correlations", fn=compute_correlations, dependencies=["calendar"]))
        orch.add_node(NodeConfig(name="signals", fn=generate_signals, dependencies=["correlations"]))
        orch.add_node(NodeConfig(name="notify", fn=notify_results, dependencies=["signals"]))

        result = orch.run()

        # Pipeline completed
        assert result.status == PipelineStatus.COMPLETED
        assert len(result.node_results) == 4
        for nr in result.node_results.values():
            assert nr.status == NodeStatus.SUCCESS

        # Calendar produced data
        assert context["is_trading_day"] is True
        assert context["session"] is not None

        # Correlation was computed
        assert context["corr_snapshot"].matrix["SPY"]["QQQ"] == pytest.approx(1.0, abs=0.01)

        # Signal was stored
        assert store.count(ticker="SPY") == 1
        spy_latest = store.get_latest("SPY")
        assert spy_latest is not None
        assert spy_latest.verdict == "BUY"
        assert "corr_window" in spy_latest.metadata

        # Notification was rendered
        assert len(context["notifications"]) == 1
        assert context["notifications"][0].channel == NotificationChannel.TELEGRAM
        assert "SPY" in context["notifications"][0].subject

        # Metrics were recorded at every step
        ref = now + timedelta(seconds=10)
        snap = mc.snapshot(window_seconds=300, now=ref)
        assert "pipeline.calendar_check" in snap
        assert "pipeline.correlation_spy_qqq" in snap
        assert "pipeline.signals_generated" in snap
        assert "pipeline.notifications_sent" in snap

    def test_failure_in_full_stack_triggers_health_notification(self) -> None:
        """When a pipeline step fails, a system_health notification is emitted."""
        engine = NotificationTemplateEngine()
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)
        notifications: list[RenderedNotification] = []

        def step_ok():
            mc.increment("pipeline.step_ok", at=now)

        def step_fail():
            raise RuntimeError("data feed offline")

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="ingest", fn=step_ok))
        orch.add_node(NodeConfig(name="process", fn=step_fail, dependencies=["ingest"]))
        orch.add_node(NodeConfig(name="publish", fn=lambda: None, dependencies=["process"]))

        result = orch.run()
        assert result.status == PipelineStatus.PARTIAL

        # Simulate alert generation for failed nodes
        for name, nr in result.node_results.items():
            if nr.status == NodeStatus.FAILED:
                rendered = engine.render(
                    "system_health",
                    {"component": name, "status": f"FAILED: {nr.error}"},
                    channel=NotificationChannel.LOG,
                )
                notifications.extend(rendered)
                mc.increment("pipeline.failure_alerts", at=now)

        assert len(notifications) == 1
        assert "process" in notifications[0].body
        assert "FAILED" in notifications[0].body

        ref = now + timedelta(seconds=10)
        assert mc.aggregate("pipeline.failure_alerts", agg="count", window_seconds=300, now=ref) == 1.0

    def test_batch_signal_scoring_with_metrics_tracking(self) -> None:
        """Batch signal scoring pipeline: score multiple tickers, track metrics."""
        store = SignalStore()
        mc = SystemMetricsCollector()
        now = datetime.now(timezone.utc)

        tickers = ["AAPL", "GOOG", "MSFT", "AMZN", "META"]
        scores = [0.82, 0.65, 0.91, 0.43, 0.77]

        def batch_score():
            snapshots = []
            for ticker, score in zip(tickers, scores):
                verdict = "BUY" if score >= 0.7 else "HOLD" if score >= 0.5 else "SELL"
                snapshots.append(SignalSnapshot(
                    ticker=ticker,
                    composite_score=score,
                    layer_scores={"alpha": score},
                    verdict=verdict,
                    confidence=score,
                ))
            ids = store.save_batch(snapshots)
            mc.record("scoring.batch_size", float(len(ids)), at=now)
            for ticker, score in zip(tickers, scores):
                mc.record("scoring.score", score, tags={"ticker": ticker}, at=now)

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig(name="batch_score", fn=batch_score))
        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        assert store.count() == 5

        buys = store.query(verdict="BUY")
        assert len(buys) == 3  # AAPL(0.82), MSFT(0.91), META(0.77)

        sells = store.query(verdict="SELL")
        assert len(sells) == 1  # AMZN(0.43)

        ref = now + timedelta(seconds=10)
        assert mc.aggregate("scoring.batch_size", agg="latest", window_seconds=300, now=ref) == 5.0
        assert mc.aggregate("scoring.score", agg="max", window_seconds=300, now=ref) == pytest.approx(0.91)
        assert mc.aggregate("scoring.score", agg="min", window_seconds=300, now=ref) == pytest.approx(0.43)
