from datetime import date

from research.market_data.corporate_actions import (
    CorporateAction,
    get_actions,
    get_adjustment_factor,
    record_action,
)
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_record_action_returns_generated_id(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(12,)])
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.corporate_actions.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.corporate_actions.release_pg_connection", lambda conn: None)

    action = record_action(
        CorporateAction(
            instrument_id=1,
            action_type="split",
            ex_date=date(2026, 3, 1),
            ratio=2.0,
        )
    )

    assert action.action_id == 12
    assert conn.committed is True


def test_get_actions_returns_models(monkeypatch):
    cursor = FakeCursor(
        fetchall_results=[
            [
                (1, 3, "split", date(2026, 1, 1), 2.0, {"source": "test"}),
                (2, 3, "dividend", date(2026, 2, 1), 0.5, {"source": "test"}),
            ]
        ],
        descriptions=[make_description("action_id", "instrument_id", "action_type", "ex_date", "ratio", "details")],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.corporate_actions.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.corporate_actions.release_pg_connection", lambda conn: None)

    actions = get_actions(3)

    assert [item.action_type for item in actions] == ["split", "dividend"]


def test_get_adjustment_factor_uses_split_actions(monkeypatch):
    monkeypatch.setattr(
        "research.market_data.corporate_actions.get_actions",
        lambda instrument_id, start, end: [
            CorporateAction(instrument_id=instrument_id, action_type="split", ex_date=date(2026, 2, 1), ratio=2.0),
            CorporateAction(instrument_id=instrument_id, action_type="dividend", ex_date=date(2026, 2, 15), ratio=1.5),
        ],
    )

    factor = get_adjustment_factor(1, date(2026, 1, 1), date(2026, 3, 1))

    assert factor == 0.5
