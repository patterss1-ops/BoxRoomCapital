from __future__ import annotations

from datetime import date
import sys

import pytest

import scripts.bootstrap_research_market_data as script


def test_build_parser_help_includes_default_and_custom_examples(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bootstrap_research_market_data.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        script._build_parser().parse_args()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out

    assert "python scripts/bootstrap_research_market_data.py" in help_text
    assert "--start 2021-01-01 --end 2026-03-10" in help_text


def test_resolve_window_defaults_to_trailing_five_years(monkeypatch):
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 10)

    monkeypatch.setattr(script, "date", _FixedDate)

    start, end = script._resolve_window(start="", end="", years=5)

    assert start == date(2021, 3, 11)
    assert end == date(2026, 3, 10)


def test_resolve_window_prefers_explicit_dates():
    start, end = script._resolve_window(start="2024-01-01", end="2024-12-31", years=5)

    assert start == date(2024, 1, 1)
    assert end == date(2024, 12, 31)


def test_resolve_window_rejects_inverted_range():
    with pytest.raises(SystemExit) as exc_info:
        script._resolve_window(start="2026-03-10", end="2026-03-09", years=5)

    assert str(exc_info.value) == "--end must be on or after --start"
