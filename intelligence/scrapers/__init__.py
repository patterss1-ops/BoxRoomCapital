"""Web scrapers for platforms without REST APIs."""

from typing import Any, Dict

# Shared SA numeric grade → letter mapping (1-13 scale)
NUMERIC_TO_GRADE: Dict[int, str] = {
    1: "F", 2: "D-", 3: "D", 4: "D+",
    5: "C-", 6: "C", 7: "C+",
    8: "B-", 9: "B", 10: "B+",
    11: "A-", 12: "A", 13: "A+",
}

# Shared rating text → normalized form
RATING_MAP: Dict[str, str] = {
    "strong buy": "strong buy", "buy": "buy", "hold": "hold",
    "neutral": "hold", "sell": "sell", "strong sell": "strong sell",
    "very bullish": "very bullish", "bullish": "bullish",
    "bearish": "bearish", "very bearish": "very bearish",
}


def normalize_rating(value: Any) -> str:
    """Normalize a rating string to a canonical form."""
    clean = " ".join(str(value or "").strip().lower().split())
    if not clean:
        return ""
    return RATING_MAP.get(clean, clean)
