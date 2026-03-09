"""Operational helpers for seeding and ingesting research market data."""

from __future__ import annotations

from datetime import date, timedelta
import json

from research.market_data.canonical_bars import get_canonical_bars, reprocess_bars
from research.market_data.ingestion import IBKRAdapter, VendorAdapter
from research.market_data.instruments import InstrumentMaster, get_instrument
from research.market_data.raw_bars import get_latest_bar, ingest_bars
from research.market_data.seed_universe import seed_mvp_universe
from research.market_data.universe import get_universe_as_of


DEFAULT_UNIVERSES = ("research_mvp", "research_proxy_mvp")


def bootstrap_mvp_market_data(
    *,
    start: date,
    end: date,
    adapter: VendorAdapter | None = None,
    universes: tuple[str, ...] = DEFAULT_UNIVERSES,
) -> dict[str, object]:
    """Seed the MVP universe and ingest missing daily history for it."""
    seed_summary = seed_mvp_universe(as_of=start)
    ingest_summary = ingest_seeded_market_data(
        start=start,
        end=end,
        adapter=adapter,
        universes=universes,
    )
    return {
        "seed_summary": seed_summary,
        "ingest_summary": ingest_summary,
        "readiness": market_data_readiness(as_of=end, universes=universes),
    }


def ingest_seeded_market_data(
    *,
    start: date,
    end: date,
    adapter: VendorAdapter | None = None,
    universes: tuple[str, ...] = DEFAULT_UNIVERSES,
) -> dict[str, object]:
    """Fetch missing daily history for seeded instruments and reprocess canonical bars."""
    if end < start:
        raise ValueError("end must be on or after start")

    vendor = adapter or IBKRAdapter()
    symbols = _seeded_instruments(as_of=end, universes=universes)
    results: list[dict[str, object]] = []
    bars_ingested = 0
    canonical_rebuilt = 0

    for instrument in symbols:
        latest = get_latest_bar(int(instrument.instrument_id or 0), vendor=vendor.vendor_name())
        fetch_start = start
        if latest is not None:
            fetch_start = max(start, latest.bar_timestamp.date() + timedelta(days=1))
        if fetch_start > end:
            results.append(
                {
                    "symbol": instrument.symbol,
                    "vendor_symbol": _vendor_symbol(instrument),
                    "status": "up_to_date",
                    "bars_ingested": 0,
                    "canonical_rebuilt": 0,
                }
            )
            continue

        fetched = vendor.fetch_daily_bars(
            _vendor_symbol(instrument),
            fetch_start,
            end + timedelta(days=1),
            instrument_id=int(instrument.instrument_id or 0),
        )
        new_bars = [bar for bar in fetched if start <= bar.bar_timestamp.date() <= end]
        if latest is not None:
            new_bars = [bar for bar in new_bars if bar.bar_timestamp > latest.bar_timestamp]

        if not new_bars:
            results.append(
                {
                    "symbol": instrument.symbol,
                    "vendor_symbol": _vendor_symbol(instrument),
                    "status": "no_data",
                    "bars_ingested": 0,
                    "canonical_rebuilt": 0,
                }
            )
            continue

        bars_ingested += int(ingest_bars(new_bars))
        canonical = reprocess_bars(
            int(instrument.instrument_id or 0),
            min(bar.bar_timestamp.date() for bar in new_bars),
            max(bar.bar_timestamp.date() for bar in new_bars),
            session_template=instrument.session_template or "default",
        )
        canonical_rebuilt += len(canonical)
        results.append(
            {
                "symbol": instrument.symbol,
                "vendor_symbol": _vendor_symbol(instrument),
                "status": "ingested",
                "bars_ingested": len(new_bars),
                "canonical_rebuilt": len(canonical),
            }
        )

    return {
        "vendor": vendor.vendor_name(),
        "instrument_count": len(symbols),
        "bars_ingested": bars_ingested,
        "canonical_rebuilt": canonical_rebuilt,
        "results": results,
    }


def market_data_readiness(
    *,
    as_of: date,
    universes: tuple[str, ...] = DEFAULT_UNIVERSES,
) -> dict[str, object]:
    """Summarize per-symbol readiness after a seed/ingest run."""
    rows: list[dict[str, object]] = []
    ready_count = 0
    for instrument in _seeded_instruments(as_of=as_of, universes=universes):
        latest = get_latest_bar(int(instrument.instrument_id or 0))
        canonical = get_canonical_bars(int(instrument.instrument_id or 0), date.min, as_of)
        latest_date = latest.bar_timestamp.date().isoformat() if latest is not None else None
        status = "ready" if canonical and latest_date else "missing"
        if status == "ready":
            ready_count += 1
        rows.append(
            {
                "symbol": instrument.symbol,
                "status": status,
                "latest_raw_bar": latest_date,
                "canonical_count": len(canonical),
                "session_template": instrument.session_template,
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "instrument_count": len(rows),
        "ready_count": ready_count,
        "rows": rows,
    }


def _seeded_instruments(*, as_of: date, universes: tuple[str, ...]) -> list[InstrumentMaster]:
    ids: set[int] = set()
    for universe in universes:
        ids.update(get_universe_as_of(universe, as_of))
    instruments: list[InstrumentMaster] = []
    for instrument_id in sorted(ids):
        instrument = get_instrument(int(instrument_id))
        if instrument is not None:
            instruments.append(instrument)
    return instruments


def _vendor_symbol(instrument: InstrumentMaster) -> str:
    return (
        str(instrument.vendor_ids.get("yfinance") or "").strip()
        or str(instrument.vendor_ids.get("ibkr") or "").strip()
        or instrument.symbol
    )


def main() -> int:
    end = date.today()
    start = end - timedelta(days=365 * 5)
    print(
        json.dumps(
            bootstrap_mvp_market_data(start=start, end=end),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual operator entrypoint
    raise SystemExit(main())
