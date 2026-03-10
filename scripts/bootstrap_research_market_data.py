"""One-shot operator entrypoint for seeding and ingesting MVP research market data."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json

from research.market_data.bootstrap import bootstrap_mvp_market_data


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Seed and ingest MVP research market data",
        epilog=(
            "Examples:\n"
            "  Bootstrap the default trailing 5-year window ending today:\n"
            "    python scripts/bootstrap_research_market_data.py\n"
            "  Bootstrap a custom date range:\n"
            "    python scripts/bootstrap_research_market_data.py --start 2021-01-01 --end 2026-03-10"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _resolve_window(*, start: str, end: str, years: int) -> tuple[date, date]:
    resolved_end = _parse_date(end) if str(end).strip() else date.today()
    resolved_start = _parse_date(start) if str(start).strip() else resolved_end - timedelta(days=365 * years)
    if resolved_end < resolved_start:
        raise SystemExit("--end must be on or after --start")
    return resolved_start, resolved_end


def main() -> int:
    parser = _build_parser()
    parser.add_argument(
        "--start",
        default="",
        help="Optional inclusive ISO-8601 start date. Defaults to --end minus --years.",
    )
    parser.add_argument(
        "--end",
        default="",
        help="Optional inclusive ISO-8601 end date. Defaults to today.",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Trailing year window to use when --start is omitted.",
    )
    args = parser.parse_args()
    start, end = _resolve_window(start=args.start, end=args.end, years=max(1, int(args.years or 5)))
    payload = bootstrap_mvp_market_data(start=start, end=end)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
