"""Read-only IG connectivity check for demo/live modes."""

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate read-only IG connectivity",
        epilog=(
            "Examples:\n"
            "  Check the live account and market lookup using a longer timeout:\n"
            "    python scripts/check_ig_access.py --mode live --timeout 10\n"
            "  Check the demo account against a specific market EPIC:\n"
            "    python scripts/check_ig_access.py --mode demo --epic IX.D.SPTRD.DAILY.IP"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "demo", "live"),
        default="auto",
        help="Broker target to validate. 'auto' follows current config.",
    )
    parser.add_argument(
        "--epic",
        default="IX.D.SPTRD.DAILY.IP",
        help="Optional market EPIC to validate after connecting.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Read-only request timeout in seconds for account/market calls.",
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
    is_demo = _resolve_mode(args.mode)
    creds = config.ig_credentials(is_demo)
    payload: dict[str, object] = {
        "mode": "DEMO" if is_demo else "LIVE",
        "broker_mode": config.BROKER_MODE,
        "endpoint": "demo" if is_demo else "live",
        "credentials_available": bool(creds["username"] and creds["password"] and creds["api_key"]),
        "account_number_configured": bool(creds["account_number"]),
    }

    if not payload["credentials_available"]:
        payload["ok"] = False
        payload["error"] = "credentials_missing"
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    broker = IGBroker(is_demo=is_demo)
    connected = broker.connect()
    payload["ok"] = bool(connected)
    payload["base_url"] = broker.base_url
    if not connected:
        payload["error"] = "connect_failed"
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    try:
        info = broker.get_account_info(timeout=args.timeout)
        positions = broker.get_positions(timeout=args.timeout)
        market = broker.get_market_info(args.epic, timeout=args.timeout) if args.epic else None
        payload.update(
            {
                "currency": info.currency,
                "balance": info.balance,
                "equity": info.equity,
                "open_positions": len(positions),
                "market_lookup_ok": bool(market),
                "market_name": (market or {}).get("instrument", {}).get("name"),
                "market_status": (market or {}).get("snapshot", {}).get("marketStatus"),
            }
        )
    finally:
        broker.disconnect()

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    raise SystemExit(main())
