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
    now = datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("chain-1", "engine_b", "AAPL", "underreaction_revision", "challenge", "revise", 72.5, now, now),
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
    )

    rows = service.active_hypotheses(limit=5)

    assert rows == [
        {
            "chain_id": "chain-1",
            "ticker": "AAPL",
            "edge_family": "underreaction_revision",
            "stage": "challenge",
            "outcome": "revise",
            "score": 72.5,
            "created_at": "2026-03-09T02:00:00Z",
            "updated_at": "2026-03-09T02:00:00Z",
        }
    ]


def test_recent_decisions_returns_acknowledged_reviews():
    now = datetime(2026, 3, 9, 3, 0, tzinfo=timezone.utc)
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
    )

    rows = service.recent_decisions(limit=5)

    assert rows[0]["strategy_id"] == "engine_a"
    assert rows[0]["decision"] == "park"
    assert rows[0]["decided_at"] == "2026-03-09T03:10:00Z"


def test_alerts_separate_pending_reviews_and_retirements():
    now = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("artifact-1", "chain-1", "ES", "engine_a", "warning", ["pf_drop", "drawdown"], "revise", now),
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
    )

    alerts = service.alerts(limit=5)

    assert alerts["pending_reviews"][0]["strategy_id"] == "engine_a"
    assert alerts["pending_reviews"][0]["flags"] == ["pf_drop", "drawdown"]
    assert alerts["kill_alerts"][0]["trigger"] == "drawdown"
    assert alerts["kill_alerts"][0]["final_status"] == "dead"
