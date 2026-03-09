from __future__ import annotations

import functools

import anyio.to_thread
import fastapi.concurrency
import fastapi.routing
import pytest
import starlette.concurrency


@pytest.fixture(autouse=True)
def _patch_broken_anyio_threadpool(monkeypatch):
    """Work around the current runtime hang in anyio.to_thread.run_sync().

    In this environment, anyio's thread offload path blocks indefinitely.
    FastAPI/Starlette sync endpoints rely on that helper, so replace it with
    inline execution for test execution.
    """

    async def run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
        return func(*args)

    async def run_in_threadpool(func, *args, **kwargs):
        if kwargs:
            func = functools.partial(func, **kwargs)
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync)
    monkeypatch.setattr(starlette.concurrency, "run_in_threadpool", run_in_threadpool)
    monkeypatch.setattr(fastapi.concurrency, "run_in_threadpool", run_in_threadpool)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", run_in_threadpool)
