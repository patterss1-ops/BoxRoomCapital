"""Futures-native helpers for Engine A research."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from data.pg_connection import get_pg_connection, release_pg_connection
from research.market_data.canonical_bars import get_canonical_bars
from research.market_data.instruments import get_instrument
from research.market_data.raw_bars import get_latest_bar
from research.shared.sql import fetchall_dicts, fetchone_dict


class FuturesContract(BaseModel):
    """Individual futures contract."""

    contract_id: int | None = None
    instrument_id: int
    root_symbol: str
    expiry_date: date
    contract_code: str
    roll_date: date | None = None
    is_front: bool = False


class RollCalendarEntry(BaseModel):
    """Scheduled roll from one contract to the next."""

    root_symbol: str
    roll_date: date
    from_contract: str
    to_contract: str
    roll_type: str = "standard"


class MultiplePrices(BaseModel):
    """Front/next/carry price snapshot for a root symbol."""

    root_symbol: str
    price_date: date
    current_contract: str
    current_price: float
    next_contract: str | None = None
    next_price: float | None = None
    carry_contract: str | None = None
    carry_price: float | None = None


class ContinuousSeries(BaseModel):
    """Back-adjusted continuous futures series."""

    root_symbol: str
    bar_date: date
    price: float
    adjustment_method: str = "panama"
    data_version: int = 1


def register_contract(contract: FuturesContract) -> FuturesContract:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            if contract.is_front:
                cur.execute(
                    "UPDATE research.futures_contracts SET is_front = FALSE WHERE root_symbol = %s",
                    (contract.root_symbol,),
                )
            cur.execute(
                """
                INSERT INTO research.futures_contracts (
                    instrument_id, root_symbol, expiry_date, contract_code, roll_date, is_front
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (root_symbol, expiry_date)
                DO UPDATE SET
                    instrument_id = EXCLUDED.instrument_id,
                    contract_code = EXCLUDED.contract_code,
                    roll_date = EXCLUDED.roll_date,
                    is_front = EXCLUDED.is_front
                RETURNING contract_id
                """,
                (
                    contract.instrument_id,
                    contract.root_symbol,
                    contract.expiry_date,
                    contract.contract_code,
                    contract.roll_date,
                    contract.is_front,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return contract.model_copy(update={"contract_id": row[0] if row else None})
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_contracts(root_symbol: str) -> list[FuturesContract]:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT contract_id, instrument_id, root_symbol, expiry_date,
                       contract_code, roll_date, is_front
                FROM research.futures_contracts
                WHERE root_symbol = %s
                ORDER BY expiry_date ASC
                """,
                (root_symbol,),
            )
            rows = fetchall_dicts(cur)
        return [FuturesContract(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def get_front_contract(root_symbol: str, as_of: date) -> FuturesContract | None:
    contracts = [
        contract for contract in get_contracts(root_symbol)
        if contract.expiry_date >= as_of
    ]
    if not contracts:
        return None
    flagged = [contract for contract in contracts if contract.is_front]
    if flagged:
        return sorted(flagged, key=lambda contract: contract.expiry_date)[0]
    return sorted(contracts, key=lambda contract: contract.expiry_date)[0]


def add_roll_entry(entry: RollCalendarEntry) -> RollCalendarEntry:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research.roll_calendar (
                    root_symbol, roll_date, from_contract, to_contract, roll_type
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (root_symbol, roll_date)
                DO UPDATE SET
                    from_contract = EXCLUDED.from_contract,
                    to_contract = EXCLUDED.to_contract,
                    roll_type = EXCLUDED.roll_type
                """,
                (
                    entry.root_symbol,
                    entry.roll_date,
                    entry.from_contract,
                    entry.to_contract,
                    entry.roll_type,
                ),
            )
        conn.commit()
        return entry
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_roll_calendar(root_symbol: str) -> list[RollCalendarEntry]:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT root_symbol, roll_date, from_contract, to_contract, roll_type
                FROM research.roll_calendar
                WHERE root_symbol = %s
                ORDER BY roll_date ASC
                """,
                (root_symbol,),
            )
            rows = fetchall_dicts(cur)
        return [RollCalendarEntry(**row) for row in rows]
    finally:
        release_pg_connection(conn)


def get_next_roll(root_symbol: str, as_of: date) -> RollCalendarEntry | None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT root_symbol, roll_date, from_contract, to_contract, roll_type
                FROM research.roll_calendar
                WHERE root_symbol = %s
                  AND roll_date >= %s
                ORDER BY roll_date ASC
                LIMIT 1
                """,
                (root_symbol, as_of),
            )
            row = fetchone_dict(cur)
        return RollCalendarEntry(**row) if row else None
    finally:
        release_pg_connection(conn)


def _latest_contract_price(contract: FuturesContract, as_of: date) -> float | None:
    canonical_bars = get_canonical_bars(contract.instrument_id, date.min, as_of)
    if canonical_bars:
        return canonical_bars[-1].close
    latest_raw = get_latest_bar(contract.instrument_id)
    if latest_raw and latest_raw.bar_timestamp.date() <= as_of:
        return latest_raw.close
    return None


def build_multiple_prices(root_symbol: str, as_of: date) -> MultiplePrices | None:
    contracts = [contract for contract in get_contracts(root_symbol) if contract.expiry_date >= as_of]
    if not contracts:
        return None

    contracts.sort(key=lambda contract: contract.expiry_date)
    current_contract = get_front_contract(root_symbol, as_of) or contracts[0]
    current_index = contracts.index(current_contract)
    next_contract = contracts[current_index + 1] if current_index + 1 < len(contracts) else None
    current_price = _latest_contract_price(current_contract, as_of)
    next_price = _latest_contract_price(next_contract, as_of) if next_contract else None
    if current_price is None:
        return None

    return MultiplePrices(
        root_symbol=root_symbol,
        price_date=as_of,
        current_contract=current_contract.contract_code,
        current_price=current_price,
        next_contract=next_contract.contract_code if next_contract else None,
        next_price=next_price,
        carry_contract=next_contract.contract_code if next_contract else None,
        carry_price=next_price,
    )


def build_continuous_series(root_symbol: str, method: str = "panama") -> list[ContinuousSeries]:
    contracts = get_contracts(root_symbol)
    if not contracts:
        return []
    contracts.sort(key=lambda contract: contract.expiry_date)
    roll_entries = {entry.from_contract: entry for entry in get_roll_calendar(root_symbol)}

    segments: list[tuple[FuturesContract, list]] = []
    for index, contract in enumerate(contracts):
        bars = get_canonical_bars(contract.instrument_id, date.min, date.max)
        if not bars:
            continue
        segment_end = contract.roll_date or roll_entries.get(contract.contract_code, None)
        end_date = segment_end.roll_date if hasattr(segment_end, "roll_date") else contract.roll_date
        if end_date is not None:
            contract_bars = [bar for bar in bars if bar.bar_date < end_date]
        else:
            contract_bars = bars
        segments.append((contract, contract_bars))

    continuous: list[ContinuousSeries] = []
    cumulative_adjustment = 0.0
    for index, (contract, bars) in enumerate(segments):
        for bar in bars:
            if bar.close is None:
                continue
            continuous.append(
                ContinuousSeries(
                    root_symbol=root_symbol,
                    bar_date=bar.bar_date,
                    price=bar.close - cumulative_adjustment,
                    adjustment_method=method,
                    data_version=bar.data_version,
                )
            )

        if index + 1 >= len(segments) or not bars:
            continue

        next_contract, next_bars = segments[index + 1]
        if not next_bars:
            continue
        overlap_date = next_bars[0].bar_date
        current_close = next((bar.close for bar in reversed(bars) if bar.close is not None), None)
        next_close = next((bar.close for bar in next_bars if bar.bar_date >= overlap_date and bar.close is not None), None)
        if current_close is not None and next_close is not None:
            cumulative_adjustment += next_close - current_close

    continuous.sort(key=lambda item: item.bar_date)
    return continuous


def get_carry_series(root_symbol: str, start: date, end: date) -> list[dict[str, object]]:
    contracts = get_contracts(root_symbol)
    if len(contracts) < 2:
        return []
    results: list[dict[str, object]] = []
    for single_date in sorted(
        {
            bar.bar_date
            for contract in contracts
            for bar in get_canonical_bars(contract.instrument_id, start, end)
        }
    ):
        if single_date < start or single_date > end:
            continue
        multiple_prices = build_multiple_prices(root_symbol, single_date)
        if not multiple_prices or multiple_prices.next_price is None:
            continue
        results.append(
            {
                "root_symbol": root_symbol,
                "price_date": single_date,
                "current_contract": multiple_prices.current_contract,
                "next_contract": multiple_prices.next_contract,
                "carry": multiple_prices.next_price - multiple_prices.current_price,
            }
        )
    return results
