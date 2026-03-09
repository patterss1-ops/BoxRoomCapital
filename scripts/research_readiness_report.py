"""Emit the current research readiness report as JSON."""

from __future__ import annotations

import json

from research.readiness import build_research_readiness_report


def main() -> int:
    print(json.dumps(build_research_readiness_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual operator entrypoint
    raise SystemExit(main())
