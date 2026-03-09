from types import SimpleNamespace

import pytest

from data import pg_connection
from tests.research_test_utils import FakeConnection, FakeCursor


class FakePool:
    def __init__(self, minconn, maxconn, dsn):
        self.minconn = minconn
        self.maxconn = maxconn
        self.dsn = dsn
        self.conn = object()
        self.returned = []

    def getconn(self):
        return self.conn

    def putconn(self, conn):
        self.returned.append(conn)

    def closeall(self):
        return None


def test_get_pg_connection_initializes_pool_once(monkeypatch):
    created = []

    def _factory(minconn, maxconn, dsn):
        pool = FakePool(minconn, maxconn, dsn)
        created.append(pool)
        return pool

    pg_connection.reset_pg_pool()
    monkeypatch.setattr(pg_connection, "_psycopg2_pool", SimpleNamespace(ThreadedConnectionPool=_factory))
    monkeypatch.setattr(pg_connection.config, "RESEARCH_DB_DSN", "postgresql://test/db")

    first = pg_connection.get_pg_connection()
    second = pg_connection.get_pg_connection()

    assert first is second
    assert len(created) == 1
    assert created[0].dsn == "postgresql://test/db"


def test_release_pg_connection_returns_to_pool(monkeypatch):
    pool = FakePool(2, 10, "postgresql://test/db")
    monkeypatch.setattr(pg_connection, "_pool", pool)

    pg_connection.release_pg_connection(pool.conn)

    assert pool.returned == [pool.conn]


def test_get_pg_connection_raises_without_driver(monkeypatch):
    pg_connection.reset_pg_pool()
    monkeypatch.setattr(pg_connection, "_psycopg2_pool", None)

    with pytest.raises(RuntimeError):
        pg_connection.get_pg_connection()


def test_init_research_schema_executes_expected_ddl(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    released = []

    monkeypatch.setattr(pg_connection, "get_pg_connection", lambda: conn)
    monkeypatch.setattr(pg_connection, "release_pg_connection", lambda connection: released.append(connection))

    pg_connection.init_research_schema()

    executed_sql = "\n".join(sql for sql, _ in cursor.executed)
    assert "CREATE SCHEMA IF NOT EXISTS research" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS research.instruments" in executed_sql
    assert "CREATE TABLE IF NOT EXISTS research.artifacts" in executed_sql
    assert conn.committed is True
    assert released == [conn]


def test_research_db_status_reports_ready_schema(monkeypatch):
    cursor = FakeCursor(fetchone_results=[("research.artifacts", "research.pipeline_state", "research.feature_cache")])
    conn = FakeConnection(cursor)

    monkeypatch.setattr(pg_connection, "_psycopg2_pool", object())
    monkeypatch.setattr(pg_connection, "get_pg_connection", lambda: conn)
    monkeypatch.setattr(pg_connection, "release_pg_connection", lambda connection: None)

    status = pg_connection.research_db_status()

    assert status["reachable"] is True
    assert status["schema_ready"] is True
    assert status["status"] == "ready"


def test_research_db_status_reports_connect_failure(monkeypatch):
    monkeypatch.setattr(pg_connection, "_psycopg2_pool", object())
    monkeypatch.setattr(pg_connection, "get_pg_connection", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pg_connection, "release_pg_connection", lambda connection: None)

    status = pg_connection.research_db_status()

    assert status["reachable"] is False
    assert status["status"] == "connect_failed"
