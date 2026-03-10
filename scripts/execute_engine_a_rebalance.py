"""Preview or execute the latest Engine A rebalance from the CLI."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from execution.dispatcher import IntentDispatcher, default_broker_resolver
from research.manual_execution import (
    execute_manual_engine_a_rebalance,
    parse_contract_details,
    preview_manual_engine_a_rebalance,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview or execute the latest Engine A rebalance")
    parser.add_argument(
        "--mode",
        choices=("paper", "demo", "live"),
        default="",
        help="Optional one-shot broker mode override for this command.",
    )
    parser.add_argument(
        "--size-mode",
        choices=("auto", "raw", "min"),
        default="auto",
        help="Sizing policy for queued orders. 'auto' uses minimum IG deal size for demo/live and raw deltas for paper.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated Engine A root symbols to execute from the latest rebalance.",
    )
    parser.add_argument("--chain-id", default="", help="Optional Engine A chain id. Defaults to the latest rebalance chain.")
    parser.add_argument("--actor", default="operator", help="Operator id recorded on approval artifacts.")
    parser.add_argument(
        "--notes",
        default="Operator approved and executed Engine A rebalance.",
        help="Operator notes stored on the approval artifact.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist approval artifacts and queue order intents.",
    )
    parser.add_argument(
        "--dispatch",
        action="store_true",
        help="Run the dispatcher once after queueing intents. Requires --commit.",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Permit commit/dispatch when broker_mode resolves to live.",
    )
    parser.add_argument(
        "--allow-live-full-size",
        action="store_true",
        help="Permit raw live quantity dispatch instead of minimum IG deal size sizing.",
    )
    parser.add_argument(
        "--smoke-close",
        action="store_true",
        help="After a live dispatch, immediately close the queued IG instruments to leave the account flat.",
    )
    parser.add_argument(
        "--close-instruments",
        default="",
        help="Comma-separated IG instruments to close without opening new positions.",
    )
    return parser.parse_args()


def _instrument_payloads(preview) -> list[dict[str, object]]:
    return [
        {
            "ticker": instrument.ticker,
            "instrument_type": instrument.instrument_type,
            "broker": instrument.broker,
            "contract_details": instrument.contract_details,
        }
        for instrument in preview.instruments
    ]


@contextmanager
def _broker_mode_override(mode: str):
    clean_mode = str(mode or "").strip().lower()
    if not clean_mode:
        yield
        return

    original_loader = config._load_runtime_overrides
    original_mode = config.BROKER_MODE
    try:
        config.BROKER_MODE = clean_mode
        config._load_runtime_overrides = lambda: {"broker_mode": clean_mode}
        yield
    finally:
        config.BROKER_MODE = original_mode
        config._load_runtime_overrides = original_loader


def _smoke_close_queued_instruments(
    queued_intents: list[dict[str, object]],
    *,
    strategy_id: str = "research_engine_a_rebalance",
    broker=None,
) -> list[dict[str, object]]:
    instruments: list[str] = []
    broker_targets = {
        str(item.get("broker_target") or "").strip().lower()
        for item in queued_intents
        if str(item.get("instrument") or "").strip()
    }
    if broker_targets != {"ig"}:
        raise ValueError("Smoke close only supports IG-queued intents.")

    for item in queued_intents:
        instrument = str(item.get("instrument") or "").strip()
        if instrument and instrument not in instruments:
            instruments.append(instrument)

    managed_broker = broker is None
    active_broker = broker or default_broker_resolver("ig")
    if managed_broker and not active_broker.connect():
        raise RuntimeError("Broker connect failed for smoke close")

    try:
        results: list[dict[str, object]] = []
        for instrument in instruments:
            close_result = active_broker.close_position(instrument, strategy_id)
            results.append(
                {
                    "instrument": instrument,
                    "ok": bool(close_result.success),
                    "deal_id": close_result.order_id,
                    "fill_price": float(close_result.fill_price or 0.0),
                    "fill_qty": float(close_result.fill_qty or 0.0),
                    "message": close_result.message,
                }
            )
        return results
    finally:
        if managed_broker:
            active_broker.disconnect()


def _ig_market_details_from_preview(preview) -> dict[str, dict[str, object]]:
    details: dict[str, dict[str, object]] = {}
    for instrument in getattr(preview, "instruments", []):
        parsed = parse_contract_details(getattr(instrument, "contract_details", ""))
        ticker = str(getattr(instrument, "ticker", "") or "").strip()
        epic = str(parsed.get("ig_epic") or "").strip()
        min_size = parsed.get("ig_min_deal_size")
        if not ticker or not epic or min_size in (None, ""):
            continue
        try:
            min_deal_size = float(min_size)
        except (TypeError, ValueError):
            continue
        if min_deal_size <= 0:
            continue
        details[ticker] = {
            "epic": epic,
            "min_deal_size": min_deal_size,
            "market_status": str(parsed.get("market_status") or ""),
        }
    return details


def main() -> int:
    args = _parse_args()
    if args.dispatch and not args.commit:
        raise SystemExit("--dispatch requires --commit")
    if args.smoke_close and not args.dispatch:
        raise SystemExit("--smoke-close requires --dispatch")

    with _broker_mode_override(args.mode):
        broker_mode = config.broker_mode()
        close_instruments = [
            item.strip()
            for item in str(args.close_instruments or "").split(",")
            if item.strip()
        ]
        symbols = [
            item.strip().upper()
            for item in str(args.symbols or "").split(",")
            if item.strip()
        ]
        payload: dict[str, object] = {
            "requested_chain_id": str(args.chain_id or ""),
            "mode_override": str(args.mode or ""),
            "broker_mode": broker_mode,
            "ig_target": "demo" if config.ig_broker_is_demo() else "live",
            "requested_size_mode": str(args.size_mode or "auto"),
            "commit": bool(args.commit),
            "dispatch": bool(args.dispatch),
            "close_instruments": list(close_instruments),
            "symbols": list(symbols),
        }

        if close_instruments:
            try:
                payload["smoke_close_results"] = _smoke_close_queued_instruments(
                    [{"instrument": instrument, "broker_target": "ig"} for instrument in close_instruments]
                )
            except Exception as exc:
                payload["ok"] = False
                payload["error"] = "close_only_failed"
                payload["message"] = str(exc)
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 1
            payload["ok"] = all(bool(item.get("ok")) for item in payload["smoke_close_results"])
            payload["status"] = "closed_only"
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if payload["ok"] else 1

        if broker_mode == "live" and (args.commit or args.dispatch) and not args.allow_live:
            payload["ok"] = False
            payload["error"] = "live_guard"
            payload["message"] = "Refusing to queue or dispatch in live mode without --allow-live."
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2

        if broker_mode == "live" and args.size_mode == "raw" and (args.commit or args.dispatch) and not args.allow_live_full_size:
            payload["ok"] = False
            payload["error"] = "live_size_guard"
            payload["message"] = "Refusing raw live quantity dispatch without --allow-live-full-size."
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2

        try:
            preview = preview_manual_engine_a_rebalance(
                chain_id=args.chain_id,
                size_mode=args.size_mode,
                symbols=symbols,
            )
        except Exception as exc:
            payload["ok"] = False
            payload["error"] = "preview_failed"
            payload["message"] = str(exc)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1

        payload.update(
            {
                "ok": True,
                "chain_id": preview.chain_id,
                "rebalance_artifact_id": preview.rebalance.artifact_id,
                "rebalance_version": int(preview.rebalance.version or 0),
                "rebalance_as_of": dict(preview.rebalance.body).get("as_of") or "",
                "broker_target": preview.broker_target,
                "resolved_size_mode": preview.size_mode,
                "deltas": dict(preview.deltas),
                "instruments": _instrument_payloads(preview),
            }
        )

        if not args.commit:
            payload["status"] = "preview_only"
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        try:
            ig_market_details = (
                _ig_market_details_from_preview(preview)
                if preview.broker_target == "ig" and preview.size_mode == "min"
                else None
            )
            result = execute_manual_engine_a_rebalance(
                chain_id=args.chain_id,
                actor=args.actor,
                notes=args.notes,
                size_mode=args.size_mode,
                symbols=symbols,
                ig_market_details=ig_market_details,
            )
        except Exception as exc:
            payload["ok"] = False
            payload["error"] = "execute_failed"
            payload["message"] = str(exc)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1

        payload.update(
            {
                "status": "queued",
                "approved_rebalance_artifact_id": result.approved_rebalance.artifact_id,
                "trade_sheet_artifact_id": result.trade_sheet.artifact_id,
                "execution_report_artifact_id": result.execution_report.artifact_id,
                "queued_intents": [
                    {
                        "intent_id": item.get("intent_id"),
                        "instrument": item.get("instrument"),
                        "broker_target": item.get("broker_target"),
                        "account_type": item.get("account_type"),
                        "side": item.get("side"),
                        "qty": item.get("qty"),
                    }
                    for item in result.queued_intents
                ],
            }
        )

        if args.dispatch:
            dispatcher = IntentDispatcher(actor="operator", disconnect_after_run=not args.smoke_close)
            try:
                payload["dispatch_summary"] = dispatcher.run_intent_ids(
                    [str(item.get("intent_id") or "") for item in result.queued_intents]
                ).to_dict()
                if args.smoke_close:
                    if broker_mode != "live":
                        payload["ok"] = False
                        payload["error"] = "smoke_close_requires_live"
                        payload["message"] = "Smoke close is only supported for live IG dispatches."
                        print(json.dumps(payload, indent=2, sort_keys=True))
                        return 2
                    broker = dispatcher._brokers.get("ig")
                    if broker is None:
                        payload["ok"] = False
                        payload["error"] = "smoke_close_failed"
                        payload["message"] = "No connected IG broker was available after dispatch."
                        print(json.dumps(payload, indent=2, sort_keys=True))
                        return 1
                    payload["smoke_close_results"] = _smoke_close_queued_instruments(
                        result.queued_intents,
                        broker=broker,
                    )
                    payload["status"] = "smoke_closed"
                    payload["ok"] = all(bool(item.get("ok")) for item in payload["smoke_close_results"])
                    if not payload["ok"]:
                        print(json.dumps(payload, indent=2, sort_keys=True))
                        return 1
            finally:
                dispatcher.disconnect_all()
            if payload.get("status") != "smoke_closed":
                payload["status"] = "dispatched"

        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    raise SystemExit(main())
