from __future__ import annotations

import sys

import pytest

import scripts.research_readiness_report as script


def test_build_parser_help_includes_readiness_example(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["research_readiness_report.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        script._build_parser().parse_args()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out

    assert "python scripts/research_readiness_report.py" in help_text
