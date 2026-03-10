from __future__ import annotations

import sys

import pytest

import scripts.run_research_validation as script


def test_parser_help_includes_engine_a_engine_b_and_all_examples(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_research_validation.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        script._build_parser().parse_args()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out

    assert "--engine engine_a" in help_text
    assert "--engine engine_b --source-class news_wire" in help_text
    assert "--engine all --source-class news_wire" in help_text


def test_parser_restricts_engine_b_source_class_to_event_schema_values():
    parser = script._build_parser()

    namespace = parser.parse_args(
        [
            "--engine",
            "engine_b",
            "--raw-content",
            "Revenue beat and guide raise.",
            "--source-class",
            "news_wire",
        ]
    )

    assert namespace.source_class == "news_wire"


def test_parser_rejects_unknown_engine_b_source_class():
    parser = script._build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "--engine",
                "engine_b",
                "--raw-content",
                "Revenue beat and guide raise.",
                "--source-class",
                "manual_operator",
            ]
        )

    assert exc_info.value.code == 2
