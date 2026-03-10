from __future__ import annotations

import json
import sys

from broker.base import OrderResult
from research.manual_execution import ManualEngineAExecutionPreview

import scripts.execute_engine_a_rebalance as script


class _FakeRebalance:
    artifact_id = "rebalance-1"
    version = 3
    body = {"as_of": "2026-03-10T17:43:31Z"}


class _FakeInstrument:
    def __init__(self, ticker: str, broker: str):
        self.ticker = ticker
        self.broker = broker
        self.instrument_type = "spread_bet" if broker == "ig" else "future"
        self.contract_details = f"ticker={ticker}"


def test_parse_args_help_includes_bounded_live_examples(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["execute_engine_a_rebalance.py", "--help"])

    try:
        script._parse_args()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("Expected argparse help to exit")

    help_text = capsys.readouterr().out

    assert "--sync-ledger" in help_text
    assert "--smoke-close" in help_text
    assert "--symbols NQ --size-mode min --commit --dispatch --allow-live --smoke-close --sync-ledger" in help_text
    assert "--close-instruments CL=F,GC=F,HG=F,NG=F,QQQ,IWM --sync-ledger" in help_text


def test_main_mode_override_uses_demo_without_global_config(monkeypatch, capsys):
    preview = ManualEngineAExecutionPreview(
        chain_id="chain-a",
        rebalance=_FakeRebalance(),
        deltas={"ES": 1.0},
        broker_target="ig",
        size_mode="min",
        instruments=[_FakeInstrument("SPY", "ig")],
    )

    monkeypatch.setattr(
        script,
        "preview_manual_engine_a_rebalance",
        lambda chain_id="", size_mode="auto", symbols=None: preview,
    )
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="demo",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=False,
        dispatch=False,
        allow_live=False,
        allow_live_full_size=False,
        smoke_close=False,
        close_instruments="",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["mode_override"] == "demo"
    assert payload["broker_mode"] == "demo"
    assert payload["ig_target"] == "demo"
    assert payload["resolved_size_mode"] == "min"
    assert payload["status"] == "preview_only"
    assert script.config.broker_mode() == "paper"


def test_main_live_guard_applies_to_mode_override(monkeypatch, capsys):
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=True,
        dispatch=False,
        allow_live=False,
        allow_live_full_size=False,
        smoke_close=False,
        close_instruments="",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["broker_mode"] == "live"
    assert payload["error"] == "live_guard"


def test_main_live_raw_size_guard_applies(monkeypatch, capsys):
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="raw",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=True,
        dispatch=False,
        allow_live=True,
        allow_live_full_size=False,
        smoke_close=False,
        close_instruments="",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["broker_mode"] == "live"
    assert payload["error"] == "live_size_guard"


class _StubCloseBroker:
    def __init__(self):
        self.calls = []

    def connect(self):
        return True

    def disconnect(self):
        return None

    def close_position(self, ticker, strategy):
        self.calls.append((ticker, strategy))
        return OrderResult(success=True, order_id=f"close-{ticker}", fill_qty=0.01)


class _SnapshotBroker:
    def __init__(self, positions):
        self._positions = list(positions)

    def get_open_positions_snapshot(self):
        return list(self._positions)


def test_smoke_close_queued_instruments_closes_unique_ig_tickers(monkeypatch):
    stub = _StubCloseBroker()
    monkeypatch.setattr(script, "default_broker_resolver", lambda name: stub)

    results = script._smoke_close_queued_instruments(
        [
            {"instrument": "SPY", "broker_target": "ig"},
            {"instrument": "QQQ", "broker_target": "ig"},
            {"instrument": "SPY", "broker_target": "ig"},
        ]
    )

    assert stub.calls == [
        ("SPY", "research_engine_a_rebalance"),
        ("QQQ", "research_engine_a_rebalance"),
    ]
    assert [item["instrument"] for item in results] == ["SPY", "QQQ"]
    assert all(item["ok"] for item in results)


def test_main_close_only_uses_smoke_close_helper(monkeypatch, capsys):
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(
        script,
        "_smoke_close_queued_instruments",
        lambda queued_intents, strategy_id="research_engine_a_rebalance", broker=None: [
            {"instrument": item["instrument"], "ok": True, "deal_id": f"close-{item['instrument']}", "fill_price": 0.0, "fill_qty": 0.01, "message": ""}
            for item in queued_intents
        ],
    )
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=False,
        dispatch=False,
        allow_live=False,
        allow_live_full_size=False,
        smoke_close=False,
        close_instruments="CL=F,GC=F",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "closed_only"
    assert [item["instrument"] for item in payload["smoke_close_results"]] == ["CL=F", "GC=F"]


def test_main_close_only_can_sync_live_ledger(monkeypatch, capsys):
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(
        script,
        "_smoke_close_queued_instruments",
        lambda queued_intents, strategy_id="research_engine_a_rebalance", broker=None: [
            {"instrument": item["instrument"], "ok": True, "deal_id": f"close-{item['instrument']}", "fill_price": 0.0, "fill_qty": 0.01, "message": ""}
            for item in queued_intents
        ],
    )
    monkeypatch.setattr(
        script,
        "_sync_live_ig_ledger_snapshot",
        lambda broker=None, account_type="SPREADBET", sleeve="core": {
            "summary": {"positions_synced": 0},
            "ledger_position_count": 0,
            "ledger_cash_balance": 8107.75,
            "ledger_buying_power": 8107.75,
        },
    )
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=False,
        dispatch=False,
        allow_live=False,
        allow_live_full_size=False,
        smoke_close=False,
        sync_ledger=True,
        close_instruments="CL=F,GC=F",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "closed_only"
    assert payload["ledger_sync"]["ledger_position_count"] == 0


def test_ig_market_details_from_preview_preserves_reference_price():
    preview = ManualEngineAExecutionPreview(
        chain_id="chain-a",
        rebalance=_FakeRebalance(),
        deltas={"NQ": 1.0},
        broker_target="ig",
        size_mode="min",
        instruments=[
            _FakeInstrument(
                "QQQ",
                "ig",
            ),
        ],
    )
    preview.instruments[0].contract_details = (
        "root_symbol=NQ;"
        "delta_contracts=1.0000;"
        "raw_order_qty=1.0000;"
        "route=ig;"
        "size_mode=min;"
        "ig_min_deal_size=0.0100;"
        "ig_epic=IX.D.NASDAQ.CASH.IP;"
        "market_status=TRADEABLE;"
        "reference_price=438.750000;"
        "order_qty=0.0100;"
        "proxy_symbol=QQQ"
    )

    details = script._ig_market_details_from_preview(preview)

    assert details == {
        "QQQ": {
            "epic": "IX.D.NASDAQ.CASH.IP",
            "min_deal_size": 0.01,
            "market_status": "TRADEABLE",
            "reference_price": 438.75,
        }
    }


def test_reconcile_live_ig_positions_flags_missing_instruments(monkeypatch):
    monkeypatch.setattr(
        script.config,
        "MARKET_MAP",
        {
            "CL=F": {"epic": "CC.D.CL.USS.IP"},
            "GC=F": {"epic": "CS.D.USCGC.TODAY.IP"},
            "QQQ": {"epic": "IX.D.NASDAQ.CASH.IP"},
        },
    )
    broker = _SnapshotBroker(
        [
            {"epic": "IX.D.NASDAQ.CASH.IP", "deal_id": "deal-qqq", "direction": "BUY", "size": 0.01},
        ]
    )

    summary = script._reconcile_live_ig_positions(
        [
            {"instrument": "CL=F"},
            {"instrument": "GC=F"},
            {"instrument": "QQQ"},
        ],
        broker=broker,
    )

    assert summary == {
        "requested": ["CL=F", "GC=F", "QQQ"],
        "open": ["QQQ"],
        "missing": ["CL=F", "GC=F"],
        "unexpected": [],
    }


def test_main_live_dispatch_returns_error_when_positions_do_not_reconcile(monkeypatch, capsys):
    preview = ManualEngineAExecutionPreview(
        chain_id="chain-a",
        rebalance=_FakeRebalance(),
        deltas={"NQ": 1.0},
        broker_target="ig",
        size_mode="min",
        instruments=[_FakeInstrument("QQQ", "ig")],
    )
    preview.instruments[0].contract_details = (
        "root_symbol=NQ;"
        "delta_contracts=1.0000;"
        "raw_order_qty=1.0000;"
        "route=ig;"
        "size_mode=min;"
        "ig_min_deal_size=0.0100;"
        "ig_epic=IX.D.NASDAQ.CASH.IP;"
        "market_status=TRADEABLE;"
        "reference_price=438.750000;"
        "order_qty=0.0100;"
        "proxy_symbol=QQQ"
    )

    class _FakeDispatcher:
        def __init__(self, actor="operator", disconnect_after_run=True):
            self._brokers = {"ig": object()}

        class _Summary:
            def to_dict(self):
                return {
                    "claim_conflicts": 0,
                    "completed": 1,
                    "discovered": 1,
                    "errors": 0,
                    "failed": 0,
                    "processed": 1,
                    "retried": 0,
                }

        def run_intent_ids(self, intent_ids):
            return self._Summary()

        def disconnect_all(self):
            return None

    class _FakeResult:
        approved_rebalance = type("Obj", (), {"artifact_id": "approved-1"})
        trade_sheet = type("Obj", (), {"artifact_id": "trade-1"})
        execution_report = type("Obj", (), {"artifact_id": "execution-1"})
        queued_intents = [
            {
                "intent_id": "intent-1",
                "instrument": "QQQ",
                "broker_target": "ig",
                "account_type": "SPREADBET",
                "side": "BUY",
                "qty": 0.01,
            }
        ]

    monkeypatch.setattr(script, "preview_manual_engine_a_rebalance", lambda **kwargs: preview)
    monkeypatch.setattr(script, "execute_manual_engine_a_rebalance", lambda **kwargs: _FakeResult())
    monkeypatch.setattr(script, "IntentDispatcher", _FakeDispatcher)
    monkeypatch.setattr(
        script,
        "_reconcile_live_ig_positions",
        lambda queued_intents, broker: {
            "requested": ["QQQ"],
            "open": [],
            "missing": ["QQQ"],
            "unexpected": [],
        },
    )
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=True,
        dispatch=True,
        allow_live=True,
        allow_live_full_size=False,
        smoke_close=False,
        close_instruments="",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["error"] == "live_position_mismatch"
    assert payload["status"] == "position_mismatch"
    assert payload["live_position_reconciliation"]["missing"] == ["QQQ"]


def test_main_dispatch_returns_error_when_dispatcher_leaves_retrying_intents(monkeypatch, capsys):
    preview = ManualEngineAExecutionPreview(
        chain_id="chain-a",
        rebalance=_FakeRebalance(),
        deltas={"CL": 1.0, "NQ": 1.0},
        broker_target="ig",
        size_mode="min",
        instruments=[_FakeInstrument("CL=F", "ig"), _FakeInstrument("QQQ", "ig")],
    )

    class _FakeDispatcher:
        def __init__(self, actor="operator", disconnect_after_run=True):
            self._brokers = {"ig": object()}

        class _Summary:
            def to_dict(self):
                return {
                    "claim_conflicts": 0,
                    "completed": 1,
                    "discovered": 2,
                    "errors": 0,
                    "failed": 0,
                    "processed": 2,
                    "retried": 1,
                }

        def run_intent_ids(self, intent_ids):
            return self._Summary()

        def disconnect_all(self):
            return None

    class _FakeResult:
        approved_rebalance = type("Obj", (), {"artifact_id": "approved-1"})
        trade_sheet = type("Obj", (), {"artifact_id": "trade-1"})
        execution_report = type("Obj", (), {"artifact_id": "execution-1"})
        queued_intents = [
            {
                "intent_id": "intent-cl",
                "instrument": "CL=F",
                "broker_target": "ig",
                "account_type": "SPREADBET",
                "side": "BUY",
                "qty": 0.01,
            },
            {
                "intent_id": "intent-qqq",
                "instrument": "QQQ",
                "broker_target": "ig",
                "account_type": "SPREADBET",
                "side": "BUY",
                "qty": 0.01,
            },
        ]

    monkeypatch.setattr(script, "preview_manual_engine_a_rebalance", lambda **kwargs: preview)
    monkeypatch.setattr(script, "execute_manual_engine_a_rebalance", lambda **kwargs: _FakeResult())
    monkeypatch.setattr(script, "IntentDispatcher", _FakeDispatcher)
    monkeypatch.setattr(
        script,
        "get_order_intent",
        lambda intent_id: {
            "intent-cl": {"status": "completed", "latest_attempt": 1},
            "intent-qqq": {"status": "retrying", "latest_attempt": 1},
        }[intent_id],
    )
    monkeypatch.setattr(
        script,
        "_reconcile_live_ig_positions",
        lambda queued_intents, broker: {
            "requested": ["CL=F", "QQQ"],
            "open": ["CL=F"],
            "missing": ["QQQ"],
            "unexpected": [],
        },
    )
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=True,
        dispatch=True,
        allow_live=True,
        allow_live_full_size=False,
        smoke_close=False,
        close_instruments="",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["error"] == "dispatch_incomplete"
    assert payload["status"] == "dispatch_incomplete"
    assert payload["dispatch_summary"]["retried"] == 1
    assert payload["intent_statuses"] == [
        {"intent_id": "intent-cl", "instrument": "CL=F", "status": "completed", "latest_attempt": 1},
        {"intent_id": "intent-qqq", "instrument": "QQQ", "status": "retrying", "latest_attempt": 1},
    ]
    assert payload["live_position_reconciliation"]["missing"] == ["QQQ"]


def test_main_live_dispatch_can_sync_ledger_after_success(monkeypatch, capsys):
    preview = ManualEngineAExecutionPreview(
        chain_id="chain-a",
        rebalance=_FakeRebalance(),
        deltas={"NQ": 1.0},
        broker_target="ig",
        size_mode="min",
        instruments=[_FakeInstrument("QQQ", "ig")],
    )

    class _FakeDispatcher:
        def __init__(self, actor="operator", disconnect_after_run=True):
            self._brokers = {"ig": object()}

        class _Summary:
            def to_dict(self):
                return {
                    "claim_conflicts": 0,
                    "completed": 1,
                    "discovered": 1,
                    "errors": 0,
                    "failed": 0,
                    "processed": 1,
                    "retried": 0,
                }

        def run_intent_ids(self, intent_ids):
            return self._Summary()

        def disconnect_all(self):
            return None

    class _FakeResult:
        approved_rebalance = type("Obj", (), {"artifact_id": "approved-1"})
        trade_sheet = type("Obj", (), {"artifact_id": "trade-1"})
        execution_report = type("Obj", (), {"artifact_id": "execution-1"})
        queued_intents = [
            {
                "intent_id": "intent-1",
                "instrument": "QQQ",
                "broker_target": "ig",
                "account_type": "SPREADBET",
                "side": "BUY",
                "qty": 0.01,
            }
        ]

    monkeypatch.setattr(script, "preview_manual_engine_a_rebalance", lambda **kwargs: preview)
    monkeypatch.setattr(script, "execute_manual_engine_a_rebalance", lambda **kwargs: _FakeResult())
    monkeypatch.setattr(script, "IntentDispatcher", _FakeDispatcher)
    monkeypatch.setattr(
        script,
        "_reconcile_live_ig_positions",
        lambda queued_intents, broker: {
            "requested": ["QQQ"],
            "open": ["QQQ"],
            "missing": [],
            "unexpected": [],
        },
    )
    monkeypatch.setattr(
        script,
        "_sync_live_ig_ledger_snapshot",
        lambda broker=None, account_type="SPREADBET", sleeve="core": {
            "summary": {"positions_synced": 1},
            "ledger_position_count": 1,
            "ledger_cash_balance": 8108.0,
            "ledger_buying_power": 8107.5,
        },
    )
    monkeypatch.setattr(script.config, "BROKER_MODE", "paper")
    monkeypatch.setattr(script.config, "_load_runtime_overrides", lambda: {})
    monkeypatch.setattr(script, "_parse_args", lambda: script.argparse.Namespace(
        mode="live",
        size_mode="auto",
        chain_id="",
        actor="operator",
        notes="notes",
        commit=True,
        dispatch=True,
        allow_live=True,
        allow_live_full_size=False,
        smoke_close=False,
        sync_ledger=True,
        close_instruments="",
        symbols="",
    ))

    exit_code = script.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "dispatched"
    assert payload["ledger_sync"]["ledger_position_count"] == 1
