from datetime import datetime, timezone

from research.dashboard import ResearchDashboardService
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_pipeline_funnel_returns_known_and_unknown_stages():
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("intake", 2),
                ("scored", 1),
                ("custom_stage", 3),
            ],
        ],
        descriptions=[make_description("current_stage", "total")],
    )
    service = ResearchDashboardService(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
    )

    stages = service.pipeline_funnel()

    assert stages[0]["stage"] == "intake"
    assert stages[0]["total"] == 2
    assert any(stage["stage"] == "scored" and stage["total"] == 1 for stage in stages)
    assert stages[-1]["stage"] == "custom_stage"
    assert stages[-1]["total"] == 3


def test_active_hypotheses_formats_pipeline_rows():
    created_at = datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
    current_time = datetime(2026, 3, 9, 4, 45, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                (
                    "chain-1",
                    "engine_b",
                    "AAPL",
                    "underreaction_revision",
                    "challenge",
                    "revise",
                    72.5,
                    created_at,
                    updated_at,
                ),
            ],
        ],
        descriptions=[
            make_description(
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "current_stage",
                "outcome",
                "score",
                "created_at",
                "updated_at",
            ),
        ],
    )
    service = ResearchDashboardService(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
        now_factory=lambda: current_time,
    )

    rows = service.active_hypotheses(limit=5)

    assert rows[0]["chain_id"] == "chain-1"
    assert rows[0]["stage"] == "challenge"
    assert rows[0]["stage_group"] == "challenge"
    assert rows[0]["next_action"] == "score and synthesize"
    assert rows[0]["updated_label"] == "45m ago"
    assert rows[0]["created_label"] == "2h ago"
    assert rows[0]["freshness"] == "aging"
    assert rows[0]["operator_now"] is False
    assert rows[0]["operator_lane_label"] == ""
    assert rows[0]["operator_priority"] == ""
    assert rows[0]["board_group"] == "flow"
    assert rows[0]["flow_lane_key"] == "challenge"
    assert rows[0]["flow_lane_label"] == "Challenge"
    assert rows[0]["flow_lane_order"] == 2


def test_active_hypotheses_marks_operator_ready_rows():
    created_at = datetime(2026, 3, 9, 1, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
    current_time = datetime(2026, 3, 9, 4, 45, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                (
                    "chain-2",
                    "engine_b",
                    "NVDA",
                    "earnings_reaction",
                    "pilot_ready",
                    "promote",
                    84.0,
                    created_at,
                    updated_at,
                ),
            ],
        ],
        descriptions=[
            make_description(
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "current_stage",
                "outcome",
                "score",
                "created_at",
                "updated_at",
            ),
        ],
    )
    service = ResearchDashboardService(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
        now_factory=lambda: current_time,
    )

    rows = service.active_hypotheses(limit=5)

    assert rows[0]["stage"] == "pilot_ready"
    assert rows[0]["operator_now"] is True
    assert rows[0]["operator_lane_label"] == "Pilot Lane"
    assert rows[0]["operator_priority"] == "watch"
    assert rows[0]["board_group"] == "operator"
    assert rows[0]["flow_lane_key"] == "active"
    assert rows[0]["flow_lane_label"] == "Active"
    assert rows[0]["flow_lane_order"] == 99


def test_recent_decisions_returns_acknowledged_reviews():
    now = datetime(2026, 3, 9, 3, 0, tzinfo=timezone.utc)
    current_time = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("artifact-1", "chain-1", "ES", "engine_a", "park", "Need more data", "decay", "2026-03-09T03:10:00Z", now),
            ],
        ],
        descriptions=[
            make_description(
                "artifact_id",
                "chain_id",
                "ticker",
                "strategy_id",
                "decision",
                "notes",
                "health_status",
                "acknowledged_at",
                "created_at",
            ),
        ],
    )
    service = ResearchDashboardService(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
        now_factory=lambda: current_time,
    )

    rows = service.recent_decisions(limit=5)

    assert rows[0]["strategy_id"] == "engine_a"
    assert rows[0]["decision"] == "park"
    assert rows[0]["decided_at"] == "2026-03-09T03:10:00Z"
    assert rows[0]["decided_label"] == "50m ago"
    assert rows[0]["decision_tone"] == "warning"


def test_alerts_separate_pending_reviews_and_retirements():
    now = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
    current_time = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("artifact-1", "chain-1", "ES", "engine_a", "warning", ["pf_drop", "drawdown"], "revise", now),
            ],
            [
                (
                    "chain-3",
                    "engine_b",
                    "NVDA",
                    "earnings_reaction",
                    "pilot_ready",
                    "promote",
                    84.0,
                    datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc),
                    datetime(2026, 3, 9, 7, 30, tzinfo=timezone.utc),
                ),
            ],
            [
                ("artifact-2", "chain-2", "ES", "hyp-1", "drawdown", "max drawdown breached", "dead", now),
            ],
        ],
        descriptions=[
            make_description(
                "artifact_id",
                "chain_id",
                "ticker",
                "strategy_id",
                "health_status",
                "flags",
                "recommended_action",
                "created_at",
            ),
            make_description(
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "current_stage",
                "outcome",
                "score",
                "created_at",
                "updated_at",
            ),
            make_description(
                "artifact_id",
                "chain_id",
                "ticker",
                "hypothesis_ref",
                "trigger",
                "trigger_detail",
                "final_status",
                "created_at",
            ),
        ],
    )
    service = ResearchDashboardService(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
        now_factory=lambda: current_time,
    )

    alerts = service.alerts(limit=5)

    assert alerts["pending_reviews"][0]["strategy_id"] == "engine_a"
    assert alerts["pending_reviews"][0]["flags"] == ["pf_drop", "drawdown"]
    assert alerts["pending_reviews"][0]["created_label"] == "4h ago"
    assert alerts["pending_reviews"][0]["priority"] == "urgent"
    assert alerts["pending_pilots"][0]["ticker"] == "NVDA"
    assert alerts["pending_pilots"][0]["next_action"] == "approve or reject pilot"
    assert alerts["pending_pilots"][0]["updated_label"] == "30m ago"
    assert alerts["pending_pilots"][0]["priority"] == "watch"
    assert alerts["kill_alerts"][0]["trigger"] == "drawdown"
    assert alerts["kill_alerts"][0]["final_status"] == "dead"
    assert alerts["kill_alerts"][0]["created_label"] == "4h ago"


def test_operating_summary_prioritizes_urgent_queue_and_latest_chain():
    now = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                (
                    "chain-1",
                    "engine_b",
                    "AAPL",
                    "underreaction_revision",
                    "challenge",
                    "revise",
                    72.5,
                    datetime(2026, 3, 9, 6, 0, tzinfo=timezone.utc),
                    datetime(2026, 3, 9, 7, 40, tzinfo=timezone.utc),
                ),
                (
                    "chain-2",
                    "engine_b",
                    "NVDA",
                    "earnings_reaction",
                    "pilot_ready",
                    "promote",
                    84.0,
                    datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc),
                    datetime(2026, 3, 9, 2, 30, tzinfo=timezone.utc),
                ),
            ],
            [
                (
                    "review-1",
                    "chain-9",
                    "AAPL",
                    "engine_a",
                    "warning",
                    ["drawdown"],
                    "reject",
                    datetime(2026, 3, 9, 5, 0, tzinfo=timezone.utc),
                ),
            ],
            [
                (
                    "decision-1",
                    "chain-7",
                    "ES",
                    "engine_a",
                    "park",
                    "Need more data",
                    "decay",
                    "2026-03-09T07:10:00Z",
                    datetime(2026, 3, 9, 7, 10, tzinfo=timezone.utc),
                ),
            ],
        ],
        descriptions=[
            make_description(
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "current_stage",
                "outcome",
                "score",
                "created_at",
                "updated_at",
            ),
            make_description(
                "artifact_id",
                "chain_id",
                "ticker",
                "strategy_id",
                "health_status",
                "flags",
                "recommended_action",
                "created_at",
            ),
            make_description(
                "artifact_id",
                "chain_id",
                "ticker",
                "strategy_id",
                "decision",
                "notes",
                "health_status",
                "acknowledged_at",
                "created_at",
            ),
        ],
    )
    service = ResearchDashboardService(
        connection_factory=lambda: FakeConnection(cursor),
        release_factory=lambda conn: None,
        now_factory=lambda: now,
    )

    summary = service.operating_summary()

    assert summary["focus_title"] == "Urgent operator queue"
    assert summary["focus_tone"] == "urgent"
    assert summary["active_chain_count"] == 2
    assert summary["freshness_counts"] == {"fresh": 1, "aging": 0, "stale": 1}
    assert summary["pilot_ready_count"] == 1
    assert summary["urgent_review_count"] == 1
    assert summary["latest_chain"]["ticker"] == "AAPL"
    assert summary["latest_chain"]["next_action"] == "score and synthesize"
    assert summary["latest_decision"]["decision"] == "park"
