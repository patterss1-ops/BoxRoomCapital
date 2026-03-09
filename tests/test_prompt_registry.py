from tests.research_test_utils import FakeConnection, FakeCursor

from research import prompt_registry


def test_register_prompts_inserts_missing_rows(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(prompt_registry, "get_pg_connection", lambda: conn)
    monkeypatch.setattr(prompt_registry, "release_pg_connection", lambda conn: None)

    prompt_registry.register_prompts(
        {"service_a": lambda: ("v1", "system", "user")}
    )

    assert any("INSERT INTO research.prompt_hashes" in sql for sql, _ in cursor.executed)
    assert conn.committed is True


def test_check_drift_detects_prompt_change(monkeypatch):
    cursor = FakeCursor(fetchone_results=[("old", "older", "PROMPT_DRIFT")])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(prompt_registry, "get_pg_connection", lambda: conn)
    monkeypatch.setattr(prompt_registry, "release_pg_connection", lambda conn: None)

    result = prompt_registry.check_drift(
        "service_a",
        {"service_a": lambda: ("v1", "system", "user")},
    )

    assert result["status"] == "PROMPT_DRIFT"
    assert result["acknowledged"] is False


def test_acknowledge_drift_clears_status(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(prompt_registry, "get_pg_connection", lambda: conn)
    monkeypatch.setattr(prompt_registry, "release_pg_connection", lambda conn: None)

    prompt_registry.acknowledge_drift("service_a")

    assert any("UPDATE research.prompt_hashes" in sql for sql, _ in cursor.executed)
    assert conn.committed is True


def test_get_prompt_hash_returns_current_hash(monkeypatch):
    cursor = FakeCursor(fetchone_results=[("abc123",)])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(prompt_registry, "get_pg_connection", lambda: conn)
    monkeypatch.setattr(prompt_registry, "release_pg_connection", lambda conn: None)

    assert prompt_registry.get_prompt_hash("service_a") == "abc123"
