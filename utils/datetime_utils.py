"""Shared datetime utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def parse_iso_utc(raw: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (including Z suffix) into a UTC-aware datetime.

    Returns None for empty/invalid input.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
