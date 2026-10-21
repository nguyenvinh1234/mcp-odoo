"""HTTP guardrails for the isolated Ngoc Viet staging write gateway.

The MCP SDK already validates OAuth and Host/Origin headers.  This middleware
runs *inside* that authenticated Starlette stack and adds bounded request
bodies, per-subject/tool rate and concurrency limits, and a total request
deadline.  It never logs request arguments or bearer tokens.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from starlette.types import Message, Receive, Scope, Send

from .audit import record_gateway_event


def _positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(int(os.environ.get(name, str(default))), minimum)
    except ValueError:
        return default


def _positive_float(name: str, default: float) -> float:
    try:
        return max(float(os.environ.get(name, str(default))), 0.1)
    except ValueError:
        return default


class SubjectToolGuard:
    """Process-local bounded counters keyed by authenticated subject and tool."""

    def __init__(self) -> None:
        self.window = _positive_float("ODOO_MCP_NGV_RATE_WINDOW_SECONDS", 60.0)
        self.read_max = _positive_int("ODOO_MCP_NGV_RATE_READ_MAX", 60)
        self.prepare_max = _positive_int("ODOO_MCP_NGV_RATE_PREPARE_MAX", 10)
        self.execute_max = _positive_int("ODOO_MCP_NGV_RATE_EXECUTE_MAX", 5)
        self.read_concurrency = _positive_int("ODOO_MCP_NGV_CONCURRENCY_READ", 2)
        self.write_concurrency = _positive_int("ODOO_MCP_NGV_CONCURRENCY_WRITE", 1)
        self._events: dict[str, deque[float]] = {}
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _category(tool: str) -> str:
        if tool == "ngv_execute_approved_change":
            return "execute"
        if tool.startswith("ngv_prepare_"):
            return "prepare"
        return "read"

    def acquire(self, subject: str, tool: str) -> tuple[bool, str | None, int]:
        category = self._category(tool)
        maximum = {
            "read": self.read_max,
            "prepare": self.prepare_max,
            "execute": self.execute_max,
        }[category]
        concurrency = (
            self.read_concurrency if category == "read" else self.write_concurrency
        )
        key = f"{subject}:{tool}"
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            cutoff = now - self.window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= maximum:
                retry_after = max(1, int(self.window - (now - events[0])))
                return False, "rate_limited", retry_after
            if self._active.get(key, 0) >= concurrency:
                return False, "concurrency_limited", 1
            events.append(now)
            self._active[key] = self._active.get(key, 0) + 1
            if len(self._events) > 1024:
                stale = min(self._events, key=lambda item: self._events[item][-1])
                self._events.pop(stale, None)
                self._active.pop(stale, None)
        return True, None, 0

    def release(self, subject: str, tool: str) -> None:
        key = f"{subject}:{tool}"
        with self._lock:
            active = self._active.get(key, 0)
            if active <= 1:
                self._active.pop(key, None)
            else:
                self._active[key] = active - 1


class NgvHttpGuardMiddleware:
    """Pure ASGI middleware for the authenticated write MCP endpoint."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, path: str = "/mcp"):
        self.app = app
        self.path = path
        self.max_body = _positive_int("ODOO_MCP_NGV_HTTP_MAX_BODY_BYTES", 65536)
        self.timeout = _positive_float("ODOO_MCP_NGV_HTTP_TIMEOUT_SECONDS", 35.0)
        self.guard = SubjectToolGuard()

    @staticmethod
    async def _json_response(
        send: Send,
        status: int,
        code: int,
        message: str,
        request_id: Any = None,
        *,
        retry_after: int | None = None,
    ) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [(b"content-type", b"application/json")]
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode("ascii")))
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.path
        ):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_body:
                access_token = get_access_token()
                subject = (
                    access_token.client_id if access_token is not None else "anonymous"
                )
                record_gateway_event(
                    "ngv_http_guard",
                    outcome="denied",
                    client_id=None if subject == "anonymous" else subject,
                    tool="mcp_control",
                    error_code="request_too_large",
                )
                await self._json_response(
                    send, 413, -32013, "Request body exceeds the configured limit"
                )
                return
            if not message.get("more_body", False):
                break

        try:
            payload = json.loads(body or b"{}")
        except (TypeError, ValueError):
            payload = {}
        request_id = payload.get("id") if isinstance(payload, dict) else None
        params = payload.get("params") if isinstance(payload, dict) else None
        tool = params.get("name") if isinstance(params, dict) else None
        method = payload.get("method") if isinstance(payload, dict) else None
        if method != "tools/call" or not isinstance(tool, str):
            tool = "mcp_control"

        access_token = get_access_token()
        subject = access_token.client_id if access_token is not None else "anonymous"
        allowed, reason, retry_after = self.guard.acquire(subject, tool)
        if not allowed:
            record_gateway_event(
                "ngv_http_guard",
                outcome="denied",
                client_id=None if subject == "anonymous" else subject,
                tool=tool,
                error_code=reason,
            )
            await self._json_response(
                send,
                429,
                -32029,
                "Request rate or concurrency limit exceeded",
                request_id,
                retry_after=retry_after,
            )
            return

        sent_request = False

        async def replay_receive() -> Message:
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            with anyio.fail_after(self.timeout):
                await self.app(scope, replay_receive, tracked_send)
        except TimeoutError:
            record_gateway_event(
                "ngv_http_guard",
                outcome="error",
                client_id=None if subject == "anonymous" else subject,
                tool=tool,
                error_code="request_timeout",
            )
            if not response_started:
                await self._json_response(
                    send, 504, -32004, "Gateway request deadline exceeded", request_id
                )
        finally:
            self.guard.release(subject, tool)
