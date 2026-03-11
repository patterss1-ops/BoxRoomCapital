"""Atomic file writes for JSON state files."""
import json
from pathlib import Path


def atomic_write_json(path: Path, data: dict, *, indent: int = 2) -> None:
    """Write JSON data atomically using tmp+rename (POSIX atomic)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX
