import json
from types import SimpleNamespace

import anyio

from odoo_mcp import ngv_http_guard


def _scope():
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1000),
        "server": ("127.0.0.1", 8000),
    }


def _payload(tool="ngv_whoami"):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {}},
        }
    ).encode()


async def _call(middleware, body):
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(_scope(), receive, send)
    return sent


async def _ok_app(scope, receive, send):
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_http_guard_returns_413_for_large_body(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_NGV_HTTP_MAX_BODY_BYTES", "32")
    guard = ngv_http_guard.NgvHttpGuardMiddleware(_ok_app)
    sent = anyio.run(_call, guard, b"x" * 33)
    assert sent[0]["status"] == 413


def test_http_guard_rate_limits_by_subject_and_tool(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_NGV_RATE_READ_MAX", "1")
    monkeypatch.setattr(
        ngv_http_guard,
        "get_access_token",
        lambda: SimpleNamespace(client_id="staging-writer-ai"),
    )
    guard = ngv_http_guard.NgvHttpGuardMiddleware(_ok_app)
    first = anyio.run(_call, guard, _payload())
    second = anyio.run(_call, guard, _payload())
    assert first[0]["status"] == 200
    assert second[0]["status"] == 429
    assert any(name == b"retry-after" for name, _ in second[0]["headers"])


def test_subject_tool_guard_enforces_write_concurrency(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_NGV_CONCURRENCY_WRITE", "1")
    guard = ngv_http_guard.SubjectToolGuard()
    first = guard.acquire("writer", "ngv_execute_approved_change")
    second = guard.acquire("writer", "ngv_execute_approved_change")
    guard.release("writer", "ngv_execute_approved_change")
    assert first[0] is True
    assert second[0] is False
    assert second[1] == "concurrency_limited"


def test_http_guard_returns_504_on_deadline(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_NGV_HTTP_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(
        ngv_http_guard,
        "get_access_token",
        lambda: SimpleNamespace(client_id="staging-writer-ai"),
    )

    async def slow_app(scope, receive, send):
        await receive()
        await anyio.sleep(0.3)

    guard = ngv_http_guard.NgvHttpGuardMiddleware(slow_app)
    sent = anyio.run(_call, guard, _payload())
    assert sent[0]["status"] == 504
