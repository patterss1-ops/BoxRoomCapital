import json
import sqlite3
from pathlib import Path

from scripts.reset_intel_state import reset_intel_state


def _create_test_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE trade_ideas (id INTEGER PRIMARY KEY);
        CREATE TABLE idea_transitions (id INTEGER PRIMARY KEY);
        CREATE TABLE idea_research_steps (id INTEGER PRIMARY KEY);
        CREATE TABLE council_costs (id INTEGER PRIMARY KEY);
        CREATE TABLE feature_records (id INTEGER PRIMARY KEY, feature_set TEXT);
        CREATE TABLE research_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT,
            source TEXT
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            job_type TEXT,
            status TEXT,
            detail TEXT
        );
        CREATE TABLE bot_events (
            id INTEGER PRIMARY KEY,
            strategy TEXT,
            headline TEXT
        );
        """
    )
    conn.execute("INSERT INTO trade_ideas DEFAULT VALUES")
    conn.execute("INSERT INTO idea_transitions DEFAULT VALUES")
    conn.execute("INSERT INTO idea_research_steps DEFAULT VALUES")
    conn.execute("INSERT INTO council_costs DEFAULT VALUES")
    conn.execute("INSERT INTO feature_records (feature_set) VALUES (?)", ("sa_factor_grades",))
    conn.execute("INSERT INTO feature_records (feature_set) VALUES (?)", ("keep_me",))
    conn.execute(
        "INSERT INTO research_events (event_type, source) VALUES (?, ?)",
        ("intel_analysis", "intel_feed"),
    )
    conn.execute(
        "INSERT INTO research_events (event_type, source) VALUES (?, ?)",
        ("other_event", "keep_me"),
    )
    conn.execute(
        "INSERT INTO jobs (job_type, status, detail) VALUES (?, ?, ?)",
        ("engine_b_intake", "queued", "SA intel: stale item"),
    )
    conn.execute(
        "INSERT INTO jobs (job_type, status, detail) VALUES (?, ?, ?)",
        ("other_job", "queued", "keep me"),
    )
    conn.execute(
        "INSERT INTO bot_events (strategy, headline) VALUES (?, ?)",
        ("sa_intel", "SA bookmarklet ping: stale"),
    )
    conn.execute(
        "INSERT INTO bot_events (strategy, headline) VALUES (?, ?)",
        ("other_strategy", "keep me"),
    )
    conn.commit()
    conn.close()


def test_reset_intel_state_clears_runtime_snapshot_and_preserves_unrelated_state(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    runtime_dir = tmp_path / ".runtime"
    runtime_state_path = runtime_dir / "research_pipeline_state.json"

    _create_test_db(db_path)
    runtime_dir.mkdir()
    runtime_state_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-03-11T11:54:19Z",
                "engine_a": {"last_result": {"status": "ok", "as_of": "2026-03-11T11:54:19Z"}},
                "engine_b": {"last_result": {"status": "ok", "job_id": "validation-20260310b"}},
                "decay_review": {"last_result": {"status": "ok"}},
                "kill_check": {"last_result": {"status": "failed"}},
                "market_data_refresh": {"last_result": {"status": "ok"}},
            }
        ),
        encoding="utf-8",
    )

    result = reset_intel_state(
        db_path=str(db_path),
        create_backup=False,
        dry_run=False,
        runtime_state_path=str(runtime_state_path),
    )

    assert result["deleted"] == {
        "trade_ideas": 1,
        "idea_transitions": 1,
        "idea_research_steps": 1,
        "council_costs": 1,
        "feature_records_sa_factor_grades": 1,
        "research_events_intel": 1,
        "jobs_intel": 1,
        "bot_events_intel": 1,
    }
    assert result["runtime_state"] == {
        "path": str(runtime_state_path),
        "present": True,
        "before": {
            "engine_b": True,
            "decay_review": True,
            "kill_check": True,
        },
        "after": {
            "engine_b": False,
            "decay_review": False,
            "kill_check": False,
        },
        "updated": True,
    }

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM trade_ideas").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM feature_records WHERE feature_set = 'keep_me'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM research_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM bot_events").fetchone()[0] == 1
    conn.close()

    runtime_payload = json.loads(runtime_state_path.read_text(encoding="utf-8"))
    assert runtime_payload["engine_a"]["last_result"]["status"] == "ok"
    assert runtime_payload["engine_b"]["last_result"] is None
    assert runtime_payload["decay_review"]["last_result"] is None
    assert runtime_payload["kill_check"]["last_result"] is None
    assert runtime_payload["market_data_refresh"]["last_result"]["status"] == "ok"
