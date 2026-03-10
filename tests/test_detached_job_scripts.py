from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_detached_job_scripts_render_help_cleanly():
    repo_root = Path(__file__).resolve().parents[1]
    scripts = {
        "scripts/detached_job_start.sh": "Usage: scripts/detached_job_start.sh <job_name> <command...>",
        "scripts/detached_job_status.sh": "Usage: scripts/detached_job_status.sh <job_name>",
        "scripts/detached_job_stop.sh": "Usage: scripts/detached_job_stop.sh <job_name> [--force]",
        "scripts/detached_job_checkpoint.sh": "Usage: scripts/detached_job_checkpoint.sh <job_name> <message...>",
    }

    for script_path, usage_text in scripts.items():
        result = subprocess.run(
            ["bash", script_path, "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, (script_path, result.stderr, result.stdout)
        assert usage_text in result.stdout
        assert "Example" in result.stdout
