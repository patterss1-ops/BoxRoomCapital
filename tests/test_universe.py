from datetime import date

from research.market_data.universe import (
    UniverseMembership,
    add_membership,
    get_universe_as_of,
    remove_membership,
    was_member,
)
from tests.research_test_utils import FakeConnection, FakeCursor


def test_add_membership_commits(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.universe.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.universe.release_pg_connection", lambda conn: None)

    membership = add_membership(
        UniverseMembership(
            instrument_id=9,
            universe="sp500",
            from_date=date(2026, 1, 1),
        )
    )

    assert membership.instrument_id == 9
    assert conn.committed is True


def test_remove_membership_updates_end_date(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.universe.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.universe.release_pg_connection", lambda conn: None)

    remove_membership(9, "sp500", date(2026, 3, 1))

    assert conn.committed is True
    assert "UPDATE research.universe_membership" in cursor.executed[0][0]


def test_get_universe_as_of_returns_ids(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[(1,), (2,), (3,)]])
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.universe.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.universe.release_pg_connection", lambda conn: None)

    members = get_universe_as_of("sp500", date(2026, 3, 1))

    assert members == [1, 2, 3]


def test_was_member_checks_membership(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(1,)])
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.universe.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.universe.release_pg_connection", lambda conn: None)

    assert was_member(4, "sp500", date(2026, 3, 1)) is True
