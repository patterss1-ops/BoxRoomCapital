from datetime import datetime, timezone
from uuid import uuid4

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactStatus, ArtifactType, EdgeFamily, Engine
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_save_creates_artifact_and_links():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    store = ArtifactStore(connection_factory=lambda: conn, release_factory=lambda conn: None)

    target_id = str(uuid4())
    envelope = ArtifactEnvelope(
        artifact_type=ArtifactType.HYPOTHESIS_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={
            "edge_family": "underreaction_revision",
            "event_card_ref": target_id,
            "market_implied_view": "Muted expectations",
            "variant_view": "Beat propagates",
            "mechanism": "Estimate revision",
            "catalyst": "Analyst follow-through",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.8,
            "invalidators": ["Guide down"],
            "failure_regimes": [],
            "candidate_expressions": ["AAPL equity"],
            "testable_predictions": ["Positive drift over 5 sessions"],
        },
    )

    artifact_id = store.save(envelope)

    assert artifact_id == envelope.artifact_id
    assert conn.committed is True
    assert any("INSERT INTO research.artifacts" in sql for sql, _ in cursor.executed)
    assert any("INSERT INTO research.artifact_links" in sql for sql, _ in cursor.executed)


def test_save_new_version_supersedes_parent():
    parent_id = str(uuid4())
    chain_id = str(uuid4())
    cursor = FakeCursor(
        fetchone_results=[(parent_id, chain_id, 2)],
        descriptions=[make_description("artifact_id", "chain_id", "version")],
    )
    conn = FakeConnection(cursor)
    store = ArtifactStore(connection_factory=lambda: conn, release_factory=lambda conn: None)

    envelope = ArtifactEnvelope(
        artifact_type=ArtifactType.SCORING_RESULT,
        engine=Engine.ENGINE_B,
        parent_id=parent_id,
        body={
            "hypothesis_ref": "hyp-1",
            "falsification_ref": "fal-1",
            "dimension_scores": {"source_integrity": 12.0},
            "raw_total": 82.0,
            "penalties": {},
            "final_score": 82.0,
            "outcome": "promote",
            "outcome_reason": "Good enough",
            "blocking_objections": [],
        },
    )

    store.save(envelope)

    assert envelope.chain_id == chain_id
    assert envelope.version == 3
    assert any("UPDATE research.artifacts SET status = %s" in sql for sql, _ in cursor.executed)


def test_get_maps_row_to_envelope():
    artifact_id = str(uuid4())
    chain_id = str(uuid4())
    cursor = FakeCursor(
        fetchone_results=[
            (
                artifact_id,
                "event_card",
                1,
                None,
                chain_id,
                "engine_b",
                "AAPL",
                "underreaction_revision",
                "active",
                {"summary": "foo"},
                datetime(2026, 3, 8, tzinfo=timezone.utc),
                "system",
                ["tag-1"],
            )
        ],
        descriptions=[
            make_description(
                "artifact_id",
                "artifact_type",
                "version",
                "parent_id",
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "status",
                "body",
                "created_at",
                "created_by",
                "tags",
            )
        ],
    )
    conn = FakeConnection(cursor)
    store = ArtifactStore(connection_factory=lambda: conn, release_factory=lambda conn: None)

    envelope = store.get(artifact_id)

    assert envelope is not None
    assert envelope.artifact_id == artifact_id
    assert envelope.engine == Engine.ENGINE_B


def test_get_chain_and_get_latest_return_ordered_versions():
    chain_id = str(uuid4())
    rows = [
        (
            str(uuid4()),
            "event_card",
            1,
            None,
            chain_id,
            "engine_b",
            "AAPL",
            None,
            "active",
            {"summary": "v1"},
            datetime(2026, 3, 8, tzinfo=timezone.utc),
            "system",
            [],
        ),
        (
            str(uuid4()),
            "event_card",
            2,
            None,
            chain_id,
            "engine_b",
            "AAPL",
            None,
            "superseded",
            {"summary": "v2"},
            datetime(2026, 3, 9, tzinfo=timezone.utc),
            "system",
            [],
        ),
    ]
    cursor = FakeCursor(
        fetchall_results=[rows, rows],
        descriptions=[
            make_description(
                "artifact_id",
                "artifact_type",
                "version",
                "parent_id",
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "status",
                "body",
                "created_at",
                "created_by",
                "tags",
            ),
            make_description(
                "artifact_id",
                "artifact_type",
                "version",
                "parent_id",
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "status",
                "body",
                "created_at",
                "created_by",
                "tags",
            ),
        ],
    )
    conn = FakeConnection(cursor)
    store = ArtifactStore(connection_factory=lambda: conn, release_factory=lambda conn: None)

    chain = store.get_chain(chain_id)
    latest = store.get_latest(chain_id)

    assert [item.version for item in chain] == [1, 2]
    assert latest.version == 2


def test_query_count_and_get_linked():
    row = (
        str(uuid4()),
        "hypothesis_card",
        1,
        None,
        str(uuid4()),
        "engine_b",
        "AAPL",
        "underreaction_revision",
        "active",
        {"summary": "foo"},
        datetime(2026, 3, 8, tzinfo=timezone.utc),
        "system",
        ["alpha"],
    )
    cursor = FakeCursor(
        fetchall_results=[[row], [row]],
        fetchone_results=[(4,)],
        descriptions=[
            make_description(
                "artifact_id",
                "artifact_type",
                "version",
                "parent_id",
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "status",
                "body",
                "created_at",
                "created_by",
                "tags",
            ),
            make_description(
                "artifact_id",
                "artifact_type",
                "version",
                "parent_id",
                "chain_id",
                "engine",
                "ticker",
                "edge_family",
                "status",
                "body",
                "created_at",
                "created_by",
                "tags",
            ),
        ],
    )
    conn = FakeConnection(cursor)
    store = ArtifactStore(connection_factory=lambda: conn, release_factory=lambda conn: None)

    queried = store.query(
        artifact_type=ArtifactType.HYPOTHESIS_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        status=ArtifactStatus.ACTIVE,
        search_text="earnings",
    )
    linked = store.get_linked("source-id", link_type="event_card_ref")
    count = store.count(artifact_type=ArtifactType.HYPOTHESIS_CARD, engine=Engine.ENGINE_B, status=ArtifactStatus.ACTIVE)

    assert len(queried) == 1
    assert len(linked) == 1
    assert count == 4
