from __future__ import annotations

import pytest

import scripts.run_research_validation as script


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
