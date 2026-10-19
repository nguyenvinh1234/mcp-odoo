"""Ngoc Viet read-only gateway policy and per-client authorization.

The policy is deliberately loaded on every request.  That makes client or
scope revocation effective on the next MCP call and avoids stale in-process
authorization state.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token


SECURITY_PROFILE_ENV = "ODOO_MCP_SECURITY_PROFILE"
POLICY_FILE_ENV = "ODOO_MCP_NGV_CLIENT_POLICY_FILE"
STDIO_CLIENT_ID_ENV = "ODOO_MCP_NGV_STDIO_CLIENT_ID"
ALLOW_STDIO_ENV = "ODOO_MCP_NGV_ALLOW_STDIO"
NGV_PROFILE = "ngocviet-readonly"

NGV_TOOLS = frozenset(
    {
        "ngv_whoami",
        "ngv_list_leads",
        "ngv_list_customers",
        "ngv_list_quotations",
        "ngv_list_products",
        "ngv_daily_sales_summary",
    }
)

TOOL_SCOPES: dict[str, frozenset[str]] = {
    "ngv_whoami": frozenset({"odoo.read"}),
    "ngv_list_leads": frozenset({"odoo.read", "odoo.crm.read"}),
    "ngv_list_customers": frozenset({"odoo.read", "odoo.customers.read"}),
    "ngv_list_quotations": frozenset({"odoo.read", "odoo.sales.read"}),
    "ngv_list_products": frozenset({"odoo.read", "odoo.products.read"}),
    "ngv_daily_sales_summary": frozenset(
        {"odoo.read", "odoo.crm.read", "odoo.sales.read"}
    ),
}


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def ngv_profile_enabled() -> bool:
    return os.environ.get(SECURITY_PROFILE_ENV, "").strip() == NGV_PROFILE


@dataclass(frozen=True)
class ClientDecision:
    client_id: str
    instance: str
    odoo_major: int
    scopes: frozenset[str]


def _policy_path() -> Path:
    raw = os.environ.get(POLICY_FILE_ENV, "").strip()
    if not raw:
        raise ValueError(f"{POLICY_FILE_ENV} is required for {NGV_PROFILE}")
    return Path(raw).expanduser()


def load_client_policy() -> dict[str, Any]:
    path = _policy_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load Ngoc Viet client policy {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Ngoc Viet client policy must be an object with version=1")
    clients = payload.get("clients")
    if not isinstance(clients, dict) or not clients:
        raise ValueError("Ngoc Viet client policy requires a non-empty clients object")
    return payload


def _request_identity() -> tuple[str, frozenset[str], bool]:
    token = get_access_token()
    if token is not None:
        return token.client_id, frozenset(token.scopes), True
    if not _truthy(ALLOW_STDIO_ENV):
        raise PermissionError("Authenticated OAuth bearer token is required")
    client_id = os.environ.get(STDIO_CLIENT_ID_ENV, "").strip()
    if not client_id:
        raise PermissionError(
            f"{STDIO_CLIENT_ID_ENV} is required when local stdio access is enabled"
        )
    return client_id, frozenset(), False


def current_request_client_id() -> str | None:
    """Return the authenticated subject for audit without authorizing a tool."""
    token = get_access_token()
    if token is not None:
        return token.client_id
    if _truthy(ALLOW_STDIO_ENV):
        return os.environ.get(STDIO_CLIENT_ID_ENV, "").strip() or None
    return None


def authorize_ngv_tool(tool_name: str) -> ClientDecision:
    if not ngv_profile_enabled():
        raise PermissionError("Ngoc Viet read-only security profile is not enabled")
    if tool_name not in NGV_TOOLS:
        raise PermissionError(f"Tool {tool_name!r} is outside the read-only allowlist")

    client_id, token_scopes, oauth = _request_identity()
    policy = load_client_policy()
    raw_client = policy["clients"].get(client_id)
    if not isinstance(raw_client, dict) or raw_client.get("enabled") is not True:
        raise PermissionError("Client is unknown or disabled")

    tools = raw_client.get("tools")
    if not isinstance(tools, list) or tool_name not in tools:
        raise PermissionError(f"Client is not allowed to call {tool_name}")

    instance = raw_client.get("instance")
    if not isinstance(instance, str) or not instance.strip():
        raise ValueError("Client policy requires a non-empty instance")
    major = raw_client.get("odoo_major")
    if major not in {16, 20}:
        raise ValueError("Client policy odoo_major must be 16 or 20")

    configured_scopes = raw_client.get("scopes", [])
    if not isinstance(configured_scopes, list) or not all(
        isinstance(scope, str) for scope in configured_scopes
    ):
        raise ValueError("Client policy scopes must be a list of strings")
    policy_scopes = frozenset(configured_scopes)
    required = TOOL_SCOPES[tool_name]
    if not required.issubset(policy_scopes):
        raise PermissionError("Client policy lacks a required tool scope")
    if oauth and not required.issubset(token_scopes):
        raise PermissionError("Bearer token lacks a required tool scope")

    return ClientDecision(client_id, instance.strip(), int(major), policy_scopes)


def validate_ngv_startup() -> None:
    """Fail closed when a read-only deployment enables dangerous escape hatches."""
    if not ngv_profile_enabled():
        return
    load_client_policy()
    dangerous = [
        name
        for name in (
            "ODOO_MCP_ENABLE_WRITES",
            "ODOO_MCP_ALLOW_UNKNOWN_METHODS",
            "MCP_CHATTER_DIRECT",
        )
        if _truthy(name)
    ]
    if dangerous:
        raise ValueError(
            f"{NGV_PROFILE} refuses dangerous settings: {', '.join(dangerous)}"
        )
    if not os.environ.get("ODOO_MCP_AUDIT_LOG", "").strip():
        raise ValueError(f"ODOO_MCP_AUDIT_LOG is required for {NGV_PROFILE}")


def ngv_posture() -> dict[str, Any]:
    return {
        "enabled": ngv_profile_enabled(),
        "profile": os.environ.get(SECURITY_PROFILE_ENV),
        "allowed_tools": sorted(NGV_TOOLS),
        "client_policy_configured": bool(os.environ.get(POLICY_FILE_ENV, "").strip()),
        "local_stdio_enabled": _truthy(ALLOW_STDIO_ENV),
    }
