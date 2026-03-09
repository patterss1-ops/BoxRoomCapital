"""Idempotent MVP universe seeding for the research market-data layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json

from research.market_data.futures import FuturesContract, RollCalendarEntry, add_roll_entry, register_contract
from research.market_data.instruments import (
    InstrumentMaster,
    create_instrument,
    get_by_symbol,
    update_instrument,
)
from research.market_data.universe import UniverseMembership, add_membership


@dataclass(frozen=True)
class FutureSeed:
    root_symbol: str
    vendor_symbol: str
    proxy_symbol: str
    venue: str
    multiplier: float
    tick_size: float
    session_template: str


@dataclass(frozen=True)
class ProxySeed:
    symbol: str
    venue: str
    session_template: str
    proxy_for: str


FUTURE_SEEDS: tuple[FutureSeed, ...] = (
    FutureSeed("ES", "ES=F", "SPY", "CME", 50.0, 0.25, "cme_globex"),
    FutureSeed("NQ", "NQ=F", "QQQ", "CME", 20.0, 0.25, "cme_globex"),
    FutureSeed("YM", "YM=F", "DIA", "CBOT", 5.0, 1.0, "cbot"),
    FutureSeed("RTY", "RTY=F", "IWM", "CME", 50.0, 0.1, "cme_globex"),
    FutureSeed("ZN", "ZN=F", "TLT", "CBOT", 1000.0, 0.015625, "cbot"),
    FutureSeed("ZB", "ZB=F", "TLT", "CBOT", 1000.0, 0.03125, "cbot"),
    FutureSeed("ZF", "ZF=F", "IEF", "CBOT", 1000.0, 0.0078125, "cbot"),
    FutureSeed("GC", "GC=F", "GLD", "COMEX", 100.0, 0.1, "cme_globex"),
    FutureSeed("SI", "SI=F", "SLV", "COMEX", 5000.0, 0.005, "cme_globex"),
    FutureSeed("CL", "CL=F", "USO", "NYMEX", 1000.0, 0.01, "cme_globex"),
    FutureSeed("NG", "NG=F", "UNG", "NYMEX", 10000.0, 0.001, "cme_globex"),
    FutureSeed("HG", "HG=F", "DBB", "COMEX", 25000.0, 0.0005, "cme_globex"),
    FutureSeed("ZC", "ZC=F", "CORN", "CBOT", 5000.0, 0.0025, "cbot"),
    FutureSeed("ZS", "ZS=F", "SOYB", "CBOT", 5000.0, 0.0025, "cbot"),
    FutureSeed("ZW", "ZW=F", "WEAT", "CBOT", 5000.0, 0.0025, "cbot"),
    FutureSeed("6E", "6E=F", "FXE", "CME", 125000.0, 0.00005, "cme_globex"),
    FutureSeed("6B", "6B=F", "FXB", "CME", 62500.0, 0.0001, "cme_globex"),
    FutureSeed("6J", "6J=F", "FXY", "CME", 12500000.0, 0.0000005, "cme_globex"),
)

PROXY_SEEDS: tuple[ProxySeed, ...] = tuple(
    ProxySeed(seed.proxy_symbol, "SMART", "us_equity", seed.root_symbol)
    for seed in FUTURE_SEEDS
)

_MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}


def seed_mvp_universe(as_of: date | None = None) -> dict[str, int]:
    """Seed futures contracts plus ETF proxies for the initial MVP universe."""
    reference_date = as_of or date.today()
    summary = {
        "proxy_instruments": 0,
        "futures_instruments": 0,
        "contracts": 0,
        "roll_entries": 0,
        "memberships": 0,
    }

    for proxy in PROXY_SEEDS:
        instrument = _upsert_instrument(
            InstrumentMaster(
                symbol=proxy.symbol,
                asset_class="etf",
                venue=proxy.venue,
                currency="USD",
                session_template=proxy.session_template,
                vendor_ids={"ibkr": proxy.symbol, "yfinance": proxy.symbol},
                metadata={
                    "mvp_seed": True,
                    "proxy_for": proxy.proxy_for,
                    "seed_universe": "research_proxy_mvp",
                },
            )
        )
        summary["proxy_instruments"] += 1
        add_membership(
            UniverseMembership(
                instrument_id=int(instrument.instrument_id or 0),
                universe="research_proxy_mvp",
                from_date=reference_date,
            )
        )
        summary["memberships"] += 1

    for seed in FUTURE_SEEDS:
        front_contract, next_contract = _contract_pair(seed.root_symbol, reference_date)
        for contract_code, expiry_date, roll_date, is_front in (
            (front_contract["contract_code"], front_contract["expiry_date"], front_contract["roll_date"], True),
            (next_contract["contract_code"], next_contract["expiry_date"], next_contract["roll_date"], False),
        ):
            instrument = _upsert_instrument(
                InstrumentMaster(
                    symbol=contract_code,
                    asset_class="future",
                    venue=seed.venue,
                    currency="USD",
                    session_template=seed.session_template,
                    multiplier=seed.multiplier,
                    tick_size=seed.tick_size,
                    vendor_ids={"ibkr": seed.vendor_symbol, "yfinance": seed.vendor_symbol},
                    metadata={
                        "mvp_seed": True,
                        "root_symbol": seed.root_symbol,
                        "proxy_symbol": seed.proxy_symbol,
                        "seed_universe": "research_mvp",
                    },
                )
            )
            summary["futures_instruments"] += 1
            add_membership(
                UniverseMembership(
                    instrument_id=int(instrument.instrument_id or 0),
                    universe="research_mvp",
                    from_date=reference_date,
                )
            )
            register_contract(
                FuturesContract(
                    instrument_id=int(instrument.instrument_id or 0),
                    root_symbol=seed.root_symbol,
                    expiry_date=expiry_date,
                    contract_code=contract_code,
                    roll_date=roll_date,
                    is_front=is_front,
                )
            )
            summary["memberships"] += 1
            summary["contracts"] += 1

        add_roll_entry(
            RollCalendarEntry(
                root_symbol=seed.root_symbol,
                roll_date=front_contract["roll_date"],
                from_contract=front_contract["contract_code"],
                to_contract=next_contract["contract_code"],
            )
        )
        summary["roll_entries"] += 1

    return summary


def _upsert_instrument(instrument: InstrumentMaster) -> InstrumentMaster:
    existing = get_by_symbol(instrument.symbol, venue=instrument.venue, asset_class=instrument.asset_class)
    if existing is None:
        return create_instrument(instrument)
    return update_instrument(
        int(existing.instrument_id or 0),
        currency=instrument.currency,
        session_template=instrument.session_template,
        multiplier=instrument.multiplier,
        tick_size=instrument.tick_size,
        vendor_ids=instrument.vendor_ids,
        is_active=instrument.is_active,
        metadata=instrument.metadata,
    ) or existing


def _contract_pair(root_symbol: str, as_of: date) -> tuple[dict[str, date | str], dict[str, date | str]]:
    front_expiry = _next_quarter_expiry(as_of)
    next_expiry = _next_quarter_expiry(front_expiry)
    front_roll = date(front_expiry.year, front_expiry.month, max(1, front_expiry.day - 7))
    next_roll = date(next_expiry.year, next_expiry.month, max(1, next_expiry.day - 7))
    return (
        {
            "contract_code": f"{root_symbol}{_month_codes[front_expiry.month]}{str(front_expiry.year)[-2:]}",
            "expiry_date": front_expiry,
            "roll_date": front_roll,
        },
        {
            "contract_code": f"{root_symbol}{_month_codes[next_expiry.month]}{str(next_expiry.year)[-2:]}",
            "expiry_date": next_expiry,
            "roll_date": next_roll,
        },
    )


def _next_quarter_expiry(anchor: date) -> date:
    for month in (3, 6, 9, 12):
        expiry = date(anchor.year, month, 15)
        if expiry > anchor:
            return expiry
    return date(anchor.year + 1, 3, 15)


def main() -> int:
    print(json.dumps(seed_mvp_universe(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual operator entrypoint
    raise SystemExit(main())
