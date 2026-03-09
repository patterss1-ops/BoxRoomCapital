"""
Manual IG API ping smoke script.

Usage:
    python3 test_api_ping.py

This file intentionally keeps the historical name, but it is not a pytest
suite. All live broker activity is gated behind `main()` so repository test
collection does not attempt a real trade.
"""

import json
import os
import sys
import time

import requests
from dotenv import load_dotenv


BASE = "https://api.ig.com/gateway/deal"
DEFAULT_EPIC = "IX.D.SPTRD.DAILY.IP"
FALLBACK_EPIC = "IX.D.SPTRD.IFD.IP"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _require_credentials() -> tuple[str, str, str, str]:
    username = _env("IG_USERNAME")
    password = _env("IG_PASSWORD")
    api_key = _env("IG_API_KEY")
    acc_num = _env("IG_ACC_NUMBER")
    missing = [name for name, value in {
        "IG_USERNAME": username,
        "IG_PASSWORD": password,
        "IG_API_KEY": api_key,
    }.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required IG credentials: {', '.join(missing)}")
    return username, password, api_key, acc_num


def main() -> int:
    load_dotenv()

    username, password, api_key, acc_num = _require_credentials()

    print("=" * 60)
    print("API PING TEST - BUY + SELL on US 500")
    print("=" * 60)
    print()

    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "X-IG-API-KEY": api_key,
    })

    print("[1] Authenticating...")
    response = session.post(
        f"{BASE}/session",
        json={"identifier": username, "password": password},
        headers={**session.headers, "Version": "2"},
    )

    if response.status_code != 200:
        print(f"    FAILED: {response.status_code} - {response.text}")
        return 1

    session.headers.update({
        "CST": response.headers["CST"],
        "X-SECURITY-TOKEN": response.headers["X-SECURITY-TOKEN"],
    })
    auth = response.json()
    print(f"    OK - Account: {auth.get('currentAccountId')}")
    print()

    if auth.get("currentAccountId") != acc_num and acc_num:
        print(f"[1b] Switching to {acc_num}...")
        switch = session.put(
            f"{BASE}/session",
            json={"accountId": acc_num, "defaultAccount": "false"},
            headers={**session.headers, "Version": "1"},
        )
        print(f"     Switch: {switch.status_code}")
        print()

    print("[2] Fetching US 500 market info...")
    epic = DEFAULT_EPIC
    market = session.get(f"{BASE}/markets/{epic}", headers={**session.headers, "Version": "3"})
    if market.status_code != 200:
        epic = FALLBACK_EPIC
        print(f"    Trying {epic}...")
        market = session.get(f"{BASE}/markets/{epic}", headers={**session.headers, "Version": "3"})

    if market.status_code != 200:
        print(f"    Failed: {market.status_code} - {market.text[:200]}")
        return 1

    market_json = market.json()
    snapshot = market_json.get("snapshot", {})
    instrument = market_json.get("instrument", {})
    rules = market_json.get("dealingRules", {})
    min_size = rules.get("minDealSize", {}).get("value", "?")
    min_stop = rules.get("minNormalStopOrLimitDistance", {}).get("value", "?")
    print(f"    Epic:       {epic}")
    print(f"    Name:       {instrument.get('name')}")
    print(f"    Status:     {snapshot.get('marketStatus')}")
    print(f"    Bid/Offer:  {snapshot.get('bid')}/{snapshot.get('offer')}")
    print(f"    Min size:   {min_size}")
    print(f"    Min stop:   {min_stop}")
    print()

    if snapshot.get("marketStatus") != "TRADEABLE":
        print("    Market not tradeable right now. Try during US hours.")
        return 0

    print("[3] Placing BUY order (min size, with stop)...")
    stop_dist = max(float(min_stop) * 2, 20)
    order = {
        "epic": epic,
        "expiry": "DFB",
        "direction": "BUY",
        "size": str(min_size),
        "orderType": "MARKET",
        "currencyCode": "GBP",
        "forceOpen": True,
        "guaranteedStop": False,
        "stopDistance": str(stop_dist),
        "limitDistance": None,
    }
    print(f"    Payload: size={min_size}, stop={stop_dist}")
    response = session.post(f"{BASE}/positions/otc", json=order, headers={**session.headers, "Version": "2"})
    print(f"    HTTP: {response.status_code}")
    if response.status_code != 200:
        print(f"    Error: {response.text[:300]}")
        return 1

    deal_ref = response.json().get("dealReference")
    print(f"    Deal ref: {deal_ref}")
    print()

    print("[4] Confirming deal...")
    time.sleep(2)
    confirm = session.get(f"{BASE}/confirms/{deal_ref}", headers={**session.headers, "Version": "1"})
    print(f"    Confirm HTTP: {confirm.status_code}")
    if confirm.status_code == 200:
        confirm_json = confirm.json()
        status = confirm_json.get("dealStatus")
        deal_id = confirm_json.get("dealId")
        reason = confirm_json.get("reason")
        print(f"    Status: {status}")
        print(f"    Reason: {reason}")
        print(f"    Deal ID: {deal_id}")
        if status != "ACCEPTED":
            print(f"\n    ORDER REJECTED: {reason}")
            print(f"    Full response: {json.dumps(confirm_json, indent=2)}")
            time.sleep(1)
            activity = session.get(
                f"{BASE}/history/activity",
                params={"from": "2026-02-26T00:00:00", "to": "2026-02-27T00:00:00"},
                headers={**session.headers, "Version": "3"},
            )
            if activity.status_code == 200:
                activities = activity.json().get("activities", [])
                rejects = [item for item in activities if item.get("status") == "REJECTED"]
                if rejects:
                    print(f"\n    Activity log rejection: {rejects[0].get('details', {})}")
            return 1
    else:
        print(f"    Confirm failed: {confirm.status_code} - {confirm.text[:200]}")
        print("    Checking positions instead...")

    print()
    print("[5] Checking open positions...")
    time.sleep(1)
    positions = session.get(f"{BASE}/positions", headers={**session.headers, "Version": "2"})
    if positions.status_code != 200:
        print(f"    Failed: {positions.status_code}")
        return 1

    open_positions = positions.json().get("positions", [])
    print(f"    Open positions: {len(open_positions)}")
    our_position = None
    for position in open_positions:
        market_data = position.get("market", {})
        position_data = position.get("position", {})
        print(
            f"    - {market_data.get('instrumentName')}: "
            f"{position_data.get('direction')} {position_data.get('size')} @ {position_data.get('openLevel')}"
        )
        print(f"      Deal ID: {position_data.get('dealId')}")
        if market_data.get("epic") == epic:
            our_position = position

    if not our_position:
        print(f"\n    No position found for {epic}")
        return 1

    close_deal_id = our_position["position"]["dealId"]
    print(f"\n    BUY confirmed! Deal ID: {close_deal_id}")
    print()

    print("[6] Closing position (SELL)...")
    time.sleep(2)
    close_order = {
        "dealId": close_deal_id,
        "direction": "SELL",
        "size": str(min_size),
        "orderType": "MARKET",
    }
    response = session.post(
        f"{BASE}/positions/otc",
        json=close_order,
        headers={**session.headers, "Version": "1", "_method": "DELETE"},
    )
    print(f"    HTTP: {response.status_code}")
    if response.status_code != 200:
        print(f"    Close failed: {response.status_code} - {response.text[:300]}")
        return 1

    close_ref = response.json().get("dealReference")
    print(f"    Close ref: {close_ref}")
    time.sleep(2)
    close_confirm = session.get(f"{BASE}/confirms/{close_ref}", headers={**session.headers, "Version": "1"})
    if close_confirm.status_code == 200:
        close_json = close_confirm.json()
        print(f"    Close status: {close_json.get('dealStatus')}")
        print(f"    Close reason: {close_json.get('reason')}")
        print(f"    P&L: {close_json.get('profit')}")
    else:
        print(f"    Close confirm: {close_confirm.status_code}")

    print()
    print("[7] Final position check...")
    time.sleep(1)
    final_positions = session.get(f"{BASE}/positions", headers={**session.headers, "Version": "2"})
    if final_positions.status_code == 200:
        remaining = final_positions.json().get("positions", [])
        us500_left = [position for position in remaining if position.get("market", {}).get("epic") == epic]
        if not us500_left:
            print("    All clear - no US500 positions remaining")
        else:
            print(f"    WARNING: {len(us500_left)} US500 position(s) still open!")

    print()
    print("=" * 60)
    print("PING TEST COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1) from exc
