from __future__ import annotations

import json

import scripts.sync_broker_snapshot as script


class _FakeSummary:
    def to_dict(self):
        return {
            "broker": "ig",
            "account_id": "PUQ8X",
            "broker_account_id": "acct-1",
            "positions_synced": 2,
            "positions_inserted": 2,
            "positions_updated": 0,
            "positions_removed": 0,
            "cash_balance": 8108.43,
            "net_liquidation": 8107.91,
        }


def test_main_syncs_live_ig_snapshot_and_reports_ledger_state(monkeypatch, capsys):
    broker_instances = []

    class _FakeBroker:
        def __init__(self, is_demo: bool):
            broker_instances.append(is_demo)

    monkeypatch.setattr(
        script,
        "_parse_args",
        lambda: script.argparse.Namespace(
            broker="ig",
            mode="live",
            account_type="SPREADBET",
            sleeve="core",
        ),
    )
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "ig_credentials_available", lambda is_demo: True)
    monkeypatch.setattr(script.config, "ig_account_number", lambda is_demo: "PUQ8X")
    monkeypatch.setattr(script, "IGBroker", _FakeBroker)
    monkeypatch.setattr(script, "sync_broker_snapshot", lambda **kwargs: _FakeSummary())
    monkeypatch.setattr(
        script,
        "get_unified_positions",
        lambda broker=None: [
            {"broker": "ig", "account_id": "PUQ8X", "ticker": "QQQ"},
            {"broker": "ig", "account_id": "PUQ8X", "ticker": "IWM"},
        ],
    )
    monkeypatch.setattr(
        script,
        "get_latest_cash_balances",
        lambda: [
            {
                "broker": "ig",
                "account_id": "PUQ8X",
                "balance": 8108.43,
                "buying_power": 8107.91,
            }
        ],
    )

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert broker_instances == [False]
    assert payload["ok"] is True
    assert payload["endpoint"] == "live"
    assert payload["account_id"] == "PUQ8X"
    assert payload["ledger_position_count"] == 2
    assert payload["ledger_cash_balance"] == 8108.43
    assert payload["ledger_buying_power"] == 8107.91
    assert payload["summary"]["positions_synced"] == 2


def test_main_returns_error_when_ig_credentials_are_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        script,
        "_parse_args",
        lambda: script.argparse.Namespace(
            broker="ig",
            mode="demo",
            account_type="SPREADBET",
            sleeve="core",
        ),
    )
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "ig_credentials_available", lambda is_demo: False)
    monkeypatch.setattr(script.config, "ig_account_number", lambda is_demo: "")

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"] == "credentials_missing"
    assert payload["endpoint"] == "demo"
