"""One-time migration utility for council -> research-system cutover."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data.trade_db import DB_PATH, get_conn

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / ".runtime" / "research_migration" / "council_cutover.json"


@dataclass(frozen=True)
class CouncilCutoverMigrationResult:
    output_path: Path
    total_candidates: int
    added: int
    skipped: int
    idea_ids: list[str]


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ideas": []}


def _fetch_trade_ideas(db_path: str) -> list[dict[str, Any]]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                analysis_id,
                ticker,
                direction,
                confidence,
                pipeline_stage,
                created_at,
                updated_at
            FROM trade_ideas
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def migrate_existing_idea_data(
    *,
    db_path: str = DB_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> CouncilCutoverMigrationResult:
    """Export existing council-generated idea data into an idempotent cutover manifest."""
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(target_path)
    existing = {
        str(item.get("idea_id")): dict(item)
        for item in manifest.get("ideas", [])
        if item.get("idea_id")
    }

    ideas = _fetch_trade_ideas(db_path)
    added = 0
    skipped = 0

    for row in ideas:
        idea_id = str(row.get("id") or "").strip()
        if not idea_id:
            continue
        payload = {
            "idea_id": idea_id,
            "analysis_id": str(row.get("analysis_id") or ""),
            "ticker": str(row.get("ticker") or ""),
            "direction": str(row.get("direction") or ""),
            "confidence": float(row.get("confidence") or 0.0),
            "pipeline_stage": str(row.get("pipeline_stage") or ""),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }
        if idea_id in existing:
            existing[idea_id] = {**existing[idea_id], **payload}
            skipped += 1
        else:
            existing[idea_id] = payload
            added += 1

    merged_ideas = sorted(
        existing.values(),
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("idea_id") or "")),
    )
    output = {
        "migration": "council_cutover_v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_db_path": str(db_path),
        "idea_count": len(merged_ideas),
        "analysis_ids": sorted(
            {
                str(item.get("analysis_id") or "")
                for item in merged_ideas
                if str(item.get("analysis_id") or "").strip()
            }
        ),
        "ideas": merged_ideas,
    }
    target_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    return CouncilCutoverMigrationResult(
        output_path=target_path,
        total_candidates=len(ideas),
        added=added,
        skipped=skipped,
        idea_ids=[str(item.get("idea_id")) for item in merged_ideas],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export existing council idea data for research-system cutover.")
    parser.add_argument("--db-path", default=DB_PATH, help="SQLite database path containing legacy trade_ideas")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Manifest output path")
    args = parser.parse_args(argv)

    result = migrate_existing_idea_data(
        db_path=str(args.db_path),
        output_path=Path(args.output),
    )
    print(
        json.dumps(
            {
                "output_path": str(result.output_path),
                "total_candidates": result.total_candidates,
                "added": result.added,
                "skipped": result.skipped,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
