"""One-shot operator entrypoint for seeding and ingesting MVP research market data."""

from __future__ import annotations

from datetime import date, timedelta
import json

from research.market_data.bootstrap import bootstrap_mvp_market_data


def main() -> int:
    end = date.today()
    start = end - timedelta(days=365 * 5)
    payload = bootstrap_mvp_market_data(start=start, end=end)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
