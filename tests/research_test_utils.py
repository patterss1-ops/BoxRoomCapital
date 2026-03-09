"""Shared fakes for research-system unit tests."""

from __future__ import annotations


def make_description(*names: str):
    return [(name, None, None, None, None, None, None) for name in names]


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_results=None,
        fetchall_results=None,
        descriptions=None,
        rowcount=0,
    ):
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])
        self._descriptions = list(descriptions or [])
        self.description = None
        self.rowcount = rowcount
        self.executed: list[tuple[str, object]] = []
        self.executemany_calls: list[tuple[str, list[object]]] = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))
        if self._descriptions:
            self.description = self._descriptions.pop(0)

    def executemany(self, sql, seq):
        payload = list(seq)
        self.executemany_calls.append((" ".join(str(sql).split()), payload))
        if self._descriptions:
            self.description = self._descriptions.pop(0)

    def fetchone(self):
        if not self._fetchone_results:
            return None
        return self._fetchone_results.pop(0)

    def fetchall(self):
        if not self._fetchall_results:
            return []
        return self._fetchall_results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def connection_sequence(*connections):
    items = iter(connections)

    def _next():
        return next(items)

    return _next
