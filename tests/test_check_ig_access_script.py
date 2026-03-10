from __future__ import annotations

import sys

import scripts.check_ig_access as script


def test_parse_args_help_includes_live_and_demo_examples(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_ig_access.py", "--help"])

    try:
        script._parse_args()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("Expected argparse help to exit")

    help_text = capsys.readouterr().out

    assert "--mode live --timeout 10" in help_text
    assert "--mode demo --epic IX.D.SPTRD.DAILY.IP" in help_text
