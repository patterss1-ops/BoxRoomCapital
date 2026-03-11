"""Clear stale Intel / council test data from the local SQLite app state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.trade_db import DB_PATH
from utils.atomic_write import atomic_write_json


INTEL_EVENT_TYPES = (
    "intel_analysis",
    "sa_browser_capture",
    "sa_page_capture",
    "sa_symbol_capture",
)
INTEL_SIGNAL_LAYER_SOURCES = (
    "sa-browser-capture",
    "sa-quant-rapidapi",
)
INTEL_BOT_STRATEGIES = (
    "sa_debug_ping",
    "sa_symbol_capture",
    "sa_page_capture",
    "sa_quant_capture",
    "sa_intel",
    "x_intel",
)
INTEL_JOB_TYPES = (
    "intel_analysis",
    "idea_research",
    "idea_backtest",
    "sa_quant_ingest",
)
INTEL_ENGINE_B_DETAIL_PREFIXES = (
    "SA intel:%",
    "X intel:%",
    "Telegram intel:%",
    "SA quant capture:%",
)
RUNTIME_STATE_SECTIONS = (
    "engine_b",
    "decay_review",
    "kill_check",
)
DEFAULT_RUNTIME_STATE_PATH = PROJECT_ROOT / ".runtime" / "research_pipeline_state.json"


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Clear stale Intel / council data from the local app database",
        epilog=(
            "Examples:\n"
            "  Preview what would be removed:\n"
            "    python scripts/reset_intel_state.py --dry-run\n"
            "  Backup the DB and clear Intel state:\n"
            "    python scripts/reset_intel_state.py --yes"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _make_backup(db_path: str) -> str:
    src = Path(db_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = src.with_name(f"{src.name}.backup-{timestamp}-pre-intel-reset")

    with _connect(str(src)) as source_conn, sqlite3.connect(str(backup_path)) as backup_conn:
        source_conn.backup(backup_conn)

    return str(backup_path)


def _fetch_count(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(query, params).fetchone()
    return int((row[0] if row else 0) or 0)


def _collect_counts(conn: sqlite3.Connection) -> dict[str, int]:
    event_placeholders = ",".join("?" for _ in INTEL_EVENT_TYPES)
    signal_placeholders = ",".join("?" for _ in INTEL_SIGNAL_LAYER_SOURCES)
    strategy_placeholders = ",".join("?" for _ in INTEL_BOT_STRATEGIES)
    job_placeholders = ",".join("?" for _ in INTEL_JOB_TYPES)
    detail_clauses = " OR ".join("detail LIKE ?" for _ in INTEL_ENGINE_B_DETAIL_PREFIXES)

    return {
        "trade_ideas": _fetch_count(conn, "SELECT COUNT(*) FROM trade_ideas"),
        "idea_transitions": _fetch_count(conn, "SELECT COUNT(*) FROM idea_transitions"),
        "idea_research_steps": _fetch_count(conn, "SELECT COUNT(*) FROM idea_research_steps"),
        "council_costs": _fetch_count(conn, "SELECT COUNT(*) FROM council_costs"),
        "feature_records_sa_factor_grades": _fetch_count(
            conn,
            "SELECT COUNT(*) FROM feature_records WHERE feature_set = ?",
            ("sa_factor_grades",),
        ),
        "research_events_intel": _fetch_count(
            conn,
            (
                "SELECT COUNT(*) FROM research_events WHERE "
                f"event_type IN ({event_placeholders}) "
                "OR source LIKE 'intel_%' "
                f"OR (event_type = 'signal_layer' AND source IN ({signal_placeholders}))"
            ),
            (*INTEL_EVENT_TYPES, *INTEL_SIGNAL_LAYER_SOURCES),
        ),
        "jobs_intel": _fetch_count(
            conn,
            (
                "SELECT COUNT(*) FROM jobs WHERE "
                f"job_type IN ({job_placeholders}) "
                f"OR (job_type = 'engine_b_intake' AND ({detail_clauses}))"
            ),
            (*INTEL_JOB_TYPES, *INTEL_ENGINE_B_DETAIL_PREFIXES),
        ),
        "bot_events_intel": _fetch_count(
            conn,
            (
                "SELECT COUNT(*) FROM bot_events WHERE "
                f"strategy IN ({strategy_placeholders}) "
                "OR headline LIKE 'SA bookmarklet ping:%'"
            ),
            INTEL_BOT_STRATEGIES,
        ),
    }


def _delete_matching_rows(conn: sqlite3.Connection) -> dict[str, int]:
    before = _collect_counts(conn)

    conn.execute("DELETE FROM idea_research_steps")
    conn.execute("DELETE FROM idea_transitions")
    conn.execute("DELETE FROM trade_ideas")
    conn.execute("DELETE FROM council_costs")
    conn.execute("DELETE FROM feature_records WHERE feature_set = ?", ("sa_factor_grades",))

    event_placeholders = ",".join("?" for _ in INTEL_EVENT_TYPES)
    signal_placeholders = ",".join("?" for _ in INTEL_SIGNAL_LAYER_SOURCES)
    conn.execute(
        (
            "DELETE FROM research_events WHERE "
            f"event_type IN ({event_placeholders}) "
            "OR source LIKE 'intel_%' "
            f"OR (event_type = 'signal_layer' AND source IN ({signal_placeholders}))"
        ),
        (*INTEL_EVENT_TYPES, *INTEL_SIGNAL_LAYER_SOURCES),
    )

    job_placeholders = ",".join("?" for _ in INTEL_JOB_TYPES)
    detail_clauses = " OR ".join("detail LIKE ?" for _ in INTEL_ENGINE_B_DETAIL_PREFIXES)
    conn.execute(
        (
            "DELETE FROM jobs WHERE "
            f"job_type IN ({job_placeholders}) "
            f"OR (job_type = 'engine_b_intake' AND ({detail_clauses}))"
        ),
        (*INTEL_JOB_TYPES, *INTEL_ENGINE_B_DETAIL_PREFIXES),
    )

    strategy_placeholders = ",".join("?" for _ in INTEL_BOT_STRATEGIES)
    conn.execute(
        (
            "DELETE FROM bot_events WHERE "
            f"strategy IN ({strategy_placeholders}) "
            "OR headline LIKE 'SA bookmarklet ping:%'"
        ),
        INTEL_BOT_STRATEGIES,
    )

    return before


def _reset_runtime_state(runtime_state_path: Path, *, dry_run: bool) -> dict[str, Any]:
    payload = _load_json_file(runtime_state_path)
    before = {
        section: bool(
            isinstance(payload.get(section), dict)
            and payload.get(section, {}).get("last_result") is not None
        )
        for section in RUNTIME_STATE_SECTIONS
    }

    if not payload:
        return {
            "path": str(runtime_state_path),
            "present": runtime_state_path.exists(),
            "before": before,
            "after": before,
            "updated": False,
        }

    updated_payload = dict(payload)
    changed = False
    for section in RUNTIME_STATE_SECTIONS:
        existing = updated_payload.get(section)
        if isinstance(existing, dict):
            if existing.get("last_result") is not None:
                changed = True
            updated_payload[section] = {**existing, "last_result": None}
        else:
            updated_payload[section] = {"last_result": None}
    updated_payload["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    after = {section: False for section in RUNTIME_STATE_SECTIONS}
    if changed and not dry_run:
        runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(runtime_state_path, updated_payload)

    return {
        "path": str(runtime_state_path),
        "present": True,
        "before": before,
        "after": after,
        "updated": changed and not dry_run,
    }


def reset_intel_state(
    *,
    db_path: str,
    create_backup: bool = True,
    dry_run: bool = False,
    runtime_state_path: str | None = str(DEFAULT_RUNTIME_STATE_PATH),
) -> dict[str, object]:
    backup_path = _make_backup(db_path) if create_backup and not dry_run else None

    with _connect(db_path) as conn:
        before = _collect_counts(conn)
        if not dry_run:
            conn.execute("BEGIN")
            _delete_matching_rows(conn)
            conn.commit()
        after = _collect_counts(conn)

    runtime_state = (
        _reset_runtime_state(Path(runtime_state_path), dry_run=dry_run)
        if runtime_state_path
        else None
    )

    return {
        "ok": True,
        "db_path": db_path,
        "backup_path": backup_path,
        "dry_run": dry_run,
        "before": before,
        "after": after,
        "deleted": {key: before[key] - after[key] for key in before},
        "runtime_state": runtime_state,
    }


def main() -> int:
    parser = _build_parser()
    parser.add_argument("--db-path", default=DB_PATH, help="SQLite database path. Defaults to the app DB.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without deleting it.")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating a SQLite backup before deletion.")
    parser.add_argument(
        "--runtime-state-path",
        default=str(DEFAULT_RUNTIME_STATE_PATH),
        help="Persisted research runtime state JSON to scrub alongside the DB reset.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Execute the reset. Required unless --dry-run is set.",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.yes:
        raise SystemExit("Refusing to delete Intel state without --yes. Use --dry-run to preview.")

    payload = reset_intel_state(
        db_path=str(args.db_path),
        create_backup=not bool(args.no_backup),
        dry_run=bool(args.dry_run),
        runtime_state_path=str(args.runtime_state_path),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    raise SystemExit(main())
