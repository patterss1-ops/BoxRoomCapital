"""Sync one broker account snapshot into the local ledger and emit JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from broker.ig import IGBroker
from execution.ledger import get_latest_cash_balances, get_unified_positions
from execution.reconciler import sync_broker_snapshot


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync one broker account snapshot into the local ledger")
    parser.add_argument(
        "--broker",
        choices=("ig",),
        default="ig",
        help="Broker account to sync.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "demo", "live"),
        default="auto",
        help="Broker target to sync. 'auto' follows current config.",
    )
    parser.add_argument(
        "--account-type",
        default="SPREADBET",
        help="Account type label recorded in the ledger.",
    )
    parser.add_argument(
        "--sleeve",
        default="core",
        help="Sleeve label recorded on synced position rows.",
    )
    return parser.parse_args()


def _resolve_mode(mode: str) -> bool:
    if mode == "demo":
        return True
    if mode == "live":
        return False
    return config.ig_broker_is_demo()


def main() -> int:
    args = _parse_args()
    if args.broker != "ig":
        raise SystemExit(f"Unsupported broker: {args.broker}")

    is_demo = _resolve_mode(args.mode)
    account_id = config.ig_account_number(is_demo)
    payload: dict[str, object] = {
        "broker": "ig",
        "mode": "DEMO" if is_demo else "LIVE",
        "endpoint": "demo" if is_demo else "live",
        "broker_mode": config.BROKER_MODE,
        "account_id": account_id,
        "credentials_available": config.ig_credentials_available(is_demo),
        "account_number_configured": bool(account_id),
        "account_type": str(args.account_type or "SPREADBET"),
        "sleeve": str(args.sleeve or "core"),
    }

    if not payload["credentials_available"]:
        payload["ok"] = False
        payload["error"] = "credentials_missing"
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    broker = IGBroker(is_demo=is_demo)
    try:
        summary = sync_broker_snapshot(
            broker=broker,
            broker_name="ig",
            account_id=account_id,
            account_type=str(args.account_type or "SPREADBET"),
            sleeve=str(args.sleeve or "core") or None,
        )
    except Exception as exc:
        payload["ok"] = False
        payload["error"] = "sync_failed"
        payload["message"] = str(exc)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    ledger_positions = [
        row
        for row in get_unified_positions(broker="ig")
        if str(row.get("account_id") or "") == account_id
    ]
    ledger_cash_rows = [
        row
        for row in get_latest_cash_balances()
        if str(row.get("broker") or "") == "ig" and str(row.get("account_id") or "") == account_id
    ]
    latest_cash = ledger_cash_rows[-1] if ledger_cash_rows else {}

    payload.update(
        {
            "ok": True,
            "summary": summary.to_dict(),
            "ledger_position_count": len(ledger_positions),
            "ledger_cash_balance": float(latest_cash.get("balance", 0.0) or 0.0),
            "ledger_buying_power": float(latest_cash.get("buying_power", 0.0) or 0.0),
        }
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    raise SystemExit(main())
