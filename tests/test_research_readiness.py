from datetime import date

from research.readiness import build_research_readiness_report, load_pipeline_stage_counts
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_load_pipeline_stage_counts_queries_pipeline_state():
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("pilot_ready", 2),
                ("review_pending", 1),
                ("review_cleared", 4),
            ]
        ],
        descriptions=[make_description("current_stage", "total")],
    )

    counts = load_pipeline_stage_counts(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
    )

    assert counts == {
        "pilot_ready": 2,
        "review_pending": 1,
        "review_cleared": 4,
    }


def test_build_research_readiness_report_blocks_without_db(monkeypatch):
    import config as _config
    monkeypatch.setattr(_config, "RESEARCH_SYSTEM_ACTIVE", False)
    report = build_research_readiness_report(
        as_of=date(2026, 3, 9),
        pipeline_status={
            "engine_a": {"enabled": True, "configured": True, "last_result": None},
            "engine_b": {"enabled": True, "configured": True, "last_result": None},
        },
        db_status={"status": "missing_dsn", "detail": "RESEARCH_DB_DSN is empty", "schema_ready": False},
        stage_counts_loader=lambda: {"review_pending": 9},
        engine_a_diag_loader=lambda: {},
    )

    assert report["overall_status"] == "blocked"
    assert report["routing_mode"] == "mirror"
    assert report["checks"][0]["key"] == "research_db"
    assert report["checks"][0]["status"] == "blocked"
    assert report["checks"][1]["key"] == "market_data"
    assert report["checks"][1]["status"] == "blocked"
    assert report["review_pending_count"] == 0
    assert any("Provision PostgreSQL" in issue for issue in report["issues"])


def test_build_research_readiness_report_highlights_partial_data_and_pending_signoff():
    report = build_research_readiness_report(
        as_of=date(2026, 3, 9),
        pipeline_status={
            "engine_a": {
                "enabled": True,
                "configured": True,
                "last_result": {"status": "ok", "as_of": "2026-03-09T18:00:00Z", "artifacts": 4},
            },
            "engine_b": {
                "enabled": True,
                "configured": True,
                "last_result": {
                    "status": "ok",
                    "as_of": "2026-03-09T18:05:00Z",
                    "current_stage": "pilot_ready",
                    "artifact_count": 6,
                },
            },
        },
        db_status={"status": "ready", "detail": "research schema ready", "schema_ready": True},
        market_data_loader=lambda as_of: {
            "instrument_count": 3,
            "ready_count": 2,
            "rows": [
                {"symbol": "SPY", "status": "ready", "latest_raw_bar": "2026-03-09"},
                {"symbol": "QQQ", "status": "ready", "latest_raw_bar": "2026-03-09"},
                {"symbol": "TLT", "status": "missing", "latest_raw_bar": None},
            ],
        },
        stage_counts_loader=lambda: {"pilot_ready": 1, "review_pending": 2},
        engine_a_diag_loader=lambda: {"max_abs_forecast": 0.0, "nonzero_delta_count": 0},
    )

    by_key = {item["key"]: item for item in report["checks"]}

    assert report["overall_status"] == "attention"
    assert by_key["research_db"]["status"] == "ready"
    assert by_key["market_data"]["status"] == "attention"
    assert by_key["market_data"]["headline"] == "2/3 ready"
    assert by_key["market_data"]["lagging_symbols"] == ["TLT"]
    assert by_key["engine_a"]["status"] == "ready"
    assert by_key["engine_b"]["status"] == "ready"
    assert by_key["operator_queue"]["status"] == "pending"
    assert report["pilot_signoff_pending_count"] == 1
    assert report["review_pending_count"] == 2
    assert any("Resolve pending decay reviews and pilot sign-offs" in item for item in report["issues"])


def test_build_research_readiness_report_allows_manual_validation_when_service_disabled():
    report = build_research_readiness_report(
        as_of=date(2026, 3, 9),
        pipeline_status={
            "engine_a": {
                "enabled": False,
                "configured": True,
                "last_result": {"status": "ok", "as_of": "2026-03-09T18:00:00Z", "artifacts": 4},
            },
            "engine_b": {
                "enabled": False,
                "configured": True,
                "last_result": None,
            },
        },
        db_status={"status": "ready", "detail": "research schema ready", "schema_ready": True},
        market_data_loader=lambda as_of: {
            "instrument_count": 1,
            "ready_count": 1,
            "rows": [{"symbol": "SPY", "status": "ready", "latest_raw_bar": "2026-03-09"}],
        },
        stage_counts_loader=lambda: {},
        engine_a_diag_loader=lambda: {"max_abs_forecast": 0.0, "nonzero_delta_count": 0},
    )

    by_key = {item["key"]: item for item in report["checks"]}

    assert by_key["engine_a"]["status"] == "ready"
    assert by_key["engine_a"]["headline"] == "ok"
    assert by_key["engine_b"]["status"] == "pending"
    assert by_key["engine_b"]["headline"] == "disabled"


def test_build_research_readiness_report_suggests_cutover_when_green():
    report = build_research_readiness_report(
        as_of=date(2026, 3, 9),
        pipeline_status={
            "engine_a": {
                "enabled": False,
                "configured": True,
                "last_result": {"status": "ok", "as_of": "2026-03-09T18:00:00Z", "artifacts": 4},
            },
            "engine_b": {
                "enabled": False,
                "configured": True,
                "last_result": {"status": "ok", "as_of": "2026-03-09T18:05:00Z", "current_stage": "scored"},
            },
        },
        db_status={"status": "ready", "detail": "research schema ready", "schema_ready": True},
        market_data_loader=lambda as_of: {
            "instrument_count": 1,
            "ready_count": 1,
            "rows": [{"symbol": "SPY", "status": "ready", "latest_raw_bar": "2026-03-09"}],
        },
        stage_counts_loader=lambda: {},
        engine_a_diag_loader=lambda: {"max_abs_forecast": 0.0, "nonzero_delta_count": 1},
    )

    assert report["overall_status"] == "ready"
    # When RESEARCH_SYSTEM_ACTIVE is off (default) but all checks green,
    # the report includes a cutover suggestion as the only issue.
    assert len(report["issues"]) == 1
    assert "enable RESEARCH_SYSTEM_ACTIVE" in report["issues"][0]


def test_build_research_readiness_report_flags_engine_a_when_signals_exist_but_rebalance_is_flat():
    report = build_research_readiness_report(
        as_of=date(2026, 3, 10),
        pipeline_status={
            "engine_a": {
                "enabled": True,
                "configured": True,
                "last_result": {"status": "ok", "as_of": "2026-03-10T16:46:15Z", "artifacts": 3},
            },
            "engine_b": {
                "enabled": True,
                "configured": True,
                "last_result": {"status": "ok", "as_of": "2026-03-10T15:36:21Z", "current_stage": "scored"},
            },
        },
        db_status={"status": "ready", "detail": "research schema ready", "schema_ready": True},
        market_data_loader=lambda as_of: {
            "instrument_count": 1,
            "ready_count": 1,
            "rows": [{"symbol": "SPY", "status": "ready", "latest_raw_bar": "2026-03-10"}],
        },
        stage_counts_loader=lambda: {},
        engine_a_diag_loader=lambda: {"max_abs_forecast": 0.49, "nonzero_delta_count": 0},
    )

    by_key = {item["key"]: item for item in report["checks"]}

    assert report["overall_status"] == "attention"
    assert by_key["engine_a"]["status"] == "attention"
    assert by_key["engine_a"]["headline"] == "granularity_blocked"
    assert "capital base or contract granularity is too small" in by_key["engine_a"]["detail"]
    assert any("Increase ENGINE_A_CAPITAL_BASE" in issue for issue in report["issues"])
