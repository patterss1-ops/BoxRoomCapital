"""Reliability tests for startup recovery failure/recovery paths."""

from __future__ import annotations

import json
from types import SimpleNamespace

import options_runner


class _BrokerStub:
    def __init__(self, deal_ids: list[str] | None = None, error: Exception | None = None):
        self._deal_ids = deal_ids or []
        self._error = error

    def get_positions(self):
        if self._error is not None:
            raise self._error
        return [SimpleNamespace(deal_id=deal_id) for deal_id in self._deal_ids]


def _build_bot(broker: _BrokerStub) -> options_runner.OptionsBot:
    bot = options_runner.OptionsBot(mode="shadow")
    bot.broker = broker
    return bot


def test_startup_recovery_marks_pending_open_as_completed_when_broker_and_db_match(monkeypatch):
    pending_actions = [
        {
            "id": "action-1",
            "action_type": "open_spread",
            "spread_id": "SPY:abc123",
            "attempt": 0,
            "max_attempts": 3,
        }
    ]
    db_positions = [
        {
            "spread_id": "SPY:abc123",
            "short_deal_id": "D-1",
            "long_deal_id": "D-2",
        }
    ]
    updates: list[dict] = []
    events: list[tuple] = []
    controls: list[dict] = []

    monkeypatch.setattr(options_runner, "get_order_actions_by_statuses", lambda statuses, limit=500: pending_actions)
    monkeypatch.setattr(options_runner, "get_open_option_positions", lambda: db_positions)
    monkeypatch.setattr(options_runner, "update_order_action", lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(options_runner, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(options_runner, "log_control_action", lambda **kwargs: controls.append(kwargs))

    bot = _build_bot(_BrokerStub(deal_ids=["D-1", "D-2"]))
    bot._startup_recover()

    assert len(updates) == 1
    assert updates[0]["status"] == "completed"
    assert updates[0]["attempt"] == 1
    payload = json.loads(updates[0]["result_payload"])
    assert payload["startup_recovered"] is True
    assert "consistent" in payload["reason"].lower()

    assert controls and controls[-1]["action"] == "startup_recovery"
    assert "recovered=1" in controls[-1]["value"]
    assert "unresolved=0" in controls[-1]["value"]
    assert any(args[0] == "POSITION" and "Startup recovery complete" in args[1] for args, _ in events)


def test_startup_recovery_flags_stale_position_sync_as_failed(monkeypatch):
    pending_actions = [
        {
            "id": "action-2",
            "action_type": "open_spread",
            "spread_id": "SPY:stale",
            "attempt": 1,
            "max_attempts": 3,
        }
    ]
    db_positions = [
        {
            "spread_id": "SPY:stale",
            "short_deal_id": "D-10",
            "long_deal_id": "D-11",
        }
    ]
    updates: list[dict] = []
    events: list[tuple] = []

    monkeypatch.setattr(options_runner, "get_order_actions_by_statuses", lambda statuses, limit=500: pending_actions)
    monkeypatch.setattr(options_runner, "get_open_option_positions", lambda: db_positions)
    monkeypatch.setattr(options_runner, "update_order_action", lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(options_runner, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(options_runner, "log_control_action", lambda **kwargs: None)

    bot = _build_bot(_BrokerStub(deal_ids=[]))
    bot._startup_recover()

    assert len(updates) == 1
    assert updates[0]["status"] == "failed"
    assert updates[0]["attempt"] == 3
    assert updates[0]["error_code"] == "STARTUP_INCOMPLETE_OPEN"
    assert "not confirmed" in updates[0]["error_message"].lower()
    assert any(args[0] == "ERROR" and "unresolved actions" in args[1].lower() for args, _ in events)


def test_startup_recovery_handles_broker_timeout_and_preserves_failure_audit(monkeypatch):
    pending_actions = [
        {
            "id": "action-3",
            "action_type": "close_spread",
            "spread_id": "SPY:close1",
            "attempt": 0,
            "max_attempts": 2,
        }
    ]
    db_positions = [
        {
            "spread_id": "SPY:close1",
            "short_deal_id": "D-20",
            "long_deal_id": "D-21",
        }
    ]
    updates: list[dict] = []
    events: list[tuple] = []

    monkeypatch.setattr(options_runner, "get_order_actions_by_statuses", lambda statuses, limit=500: pending_actions)
    monkeypatch.setattr(options_runner, "get_open_option_positions", lambda: db_positions)
    monkeypatch.setattr(options_runner, "update_order_action", lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(options_runner, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(options_runner, "log_control_action", lambda **kwargs: None)

    bot = _build_bot(_BrokerStub(error=TimeoutError("broker timeout during recovery")))
    bot._startup_recover()

    assert len(updates) == 1
    assert updates[0]["status"] == "failed"
    assert updates[0]["error_code"] == "STARTUP_INCOMPLETE_CLOSE"
    assert updates[0]["attempt"] == 2

    event_titles = [args[1] for args, _ in events if len(args) >= 2]
    assert any("failed to fetch broker positions" in title.lower() for title in event_titles)
    assert any("unresolved actions" in title.lower() for title in event_titles)
