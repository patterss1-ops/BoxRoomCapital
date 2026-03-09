"""Minimal sync test client built directly on the ASGI callable."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit


@dataclass
class ASGIResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text or "null")


class ASGITestClient:
    """Small synchronous client for exercising the full ASGI stack in tests.

    This avoids the broken TestClient path in the current FastAPI/Starlette
    runtime while still covering middleware, routing, request parsing, and
    lifespan startup/shutdown.
    """

    def __init__(self, app, *, base_url: str = "http://testserver"):
        self.app = app
        self.base_url = base_url.rstrip("/")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lifespan = None

    def __enter__(self):
        self._loop = asyncio.new_event_loop()
        self._lifespan = self.app.router.lifespan_context(self.app)
        self._loop.run_until_complete(self._lifespan.__aenter__())
        return self

    def __exit__(self, exc_type, exc, tb):
        assert self._loop is not None
        assert self._lifespan is not None
        self._loop.run_until_complete(self._lifespan.__aexit__(exc_type, exc, tb))
        self._loop.close()
        self._loop = None
        self._lifespan = None
        return False

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> ASGIResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        content: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> ASGIResponse:
        return self.request("POST", path, json=json, data=data, content=content, headers=headers)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        content: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> ASGIResponse:
        assert self._loop is not None, "Use ASGITestClient as a context manager."
        return self._loop.run_until_complete(
            self._request(method, path, json=json, data=data, content=content, headers=headers)
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        content: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> ASGIResponse:
        raw_headers = {
            str(key).lower(): str(value)
            for key, value in (headers or {}).items()
        }

        if json is not None:
            body = json_dumps(json)
            raw_headers.setdefault("content-type", "application/json")
        elif data is not None:
            body = urlencode(data, doseq=True).encode("utf-8")
            raw_headers.setdefault("content-type", "application/x-www-form-urlencoded")
        elif isinstance(content, str):
            body = content.encode("utf-8")
        elif content is not None:
            body = content
        else:
            body = b""

        parts = urlsplit(path if "://" in path else f"{self.base_url}{path}")
        path_value = parts.path or "/"
        query_string = parts.query.encode("utf-8")
        header_items = [(key.encode("latin-1"), value.encode("latin-1")) for key, value in raw_headers.items()]
        if "host" not in raw_headers:
            header_items.append((b"host", parts.netloc.encode("latin-1") or b"testserver"))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": parts.scheme or "http",
            "path": path_value,
            "raw_path": path_value.encode("utf-8"),
            "query_string": query_string,
            "headers": header_items,
            "client": ("127.0.0.1", 123),
            "server": (parts.hostname or "testserver", parts.port or 80),
            "root_path": "",
        }

        received_body = False
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal received_body
            if not received_body:
                received_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(scope, receive, send)

        status_code = 500
        response_headers: dict[str, str] = {}
        response_body = bytearray()
        for message in messages:
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                response_headers = {
                    key.decode("latin-1"): value.decode("latin-1")
                    for key, value in message.get("headers", [])
                }
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
        return ASGIResponse(status_code=status_code, headers=response_headers, body=bytes(response_body))


def json_dumps(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")
