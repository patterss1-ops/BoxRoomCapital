from datetime import datetime, timezone

from research.market_data.snapshots import (
    SnapshotType,
    get_latest_snapshot,
    get_snapshot,
    get_snapshots,
    save_snapshot,
)
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_save_snapshot_returns_inserted_snapshot(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(42,)])
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.snapshots.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.snapshots.release_pg_connection", lambda conn: None)

    snapshot = save_snapshot(SnapshotType.REGIME, datetime(2026, 3, 8, tzinfo=timezone.utc), {"vix": 18.0})

    assert snapshot.snapshot_id == 42
    assert snapshot.snapshot_type == SnapshotType.REGIME
    assert conn.committed is True


def test_get_latest_snapshot_returns_model(monkeypatch):
    cursor = FakeCursor(
        fetchone_results=[(5, "regime", datetime(2026, 3, 8, tzinfo=timezone.utc), {"vix": 18.0})],
        descriptions=[make_description("snapshot_id", "snapshot_type", "as_of", "body")],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.snapshots.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.snapshots.release_pg_connection", lambda conn: None)

    snapshot = get_latest_snapshot(SnapshotType.REGIME)

    assert snapshot is not None
    assert snapshot.body["vix"] == 18.0


def test_get_snapshots_returns_range(monkeypatch):
    cursor = FakeCursor(
        fetchall_results=[[(1, "regime", datetime(2026, 3, 8, tzinfo=timezone.utc), {"vix": 18.0})]],
        descriptions=[make_description("snapshot_id", "snapshot_type", "as_of", "body")],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.snapshots.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.snapshots.release_pg_connection", lambda conn: None)

    snapshots = get_snapshots(
        SnapshotType.REGIME,
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 8, tzinfo=timezone.utc),
    )

    assert len(snapshots) == 1
    assert snapshots[0].snapshot_id == 1


def test_get_snapshot_returns_single_row(monkeypatch):
    cursor = FakeCursor(
        fetchone_results=[(7, "broker_account", datetime(2026, 3, 8, tzinfo=timezone.utc), {"cash": 1000.0})],
        descriptions=[make_description("snapshot_id", "snapshot_type", "as_of", "body")],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.snapshots.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.snapshots.release_pg_connection", lambda conn: None)

    snapshot = get_snapshot(7)

    assert snapshot is not None
    assert snapshot.snapshot_type == SnapshotType.BROKER_ACCOUNT
