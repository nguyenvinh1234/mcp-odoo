"""Run a sanitized MCP/Odoo ACL smoke test against the isolated staging stack."""

from __future__ import annotations

import asyncio
import json
import os
import xmlrpc.client
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


URL = os.environ.get("MCP_TEST_URL", "http://gateway:8000/mcp")
TOKEN_URL = os.environ["MCP_AUTH_TOKEN_URL"]
REVOCATION_URL = os.environ["MCP_AUTH_REVOCATION_URL"]
CLIENT_ID = "ngv-odoo-mcp-staging"
FULL_IDENTITY = "staging-full-ai"
CRM_IDENTITY = "staging-crm-ai"
REQUESTED_SCOPES = " ".join(
    [
        "odoo.read",
        "odoo.crm.read",
        "odoo.customers.read",
        "odoo.sales.read",
        "odoo.products.read",
    ]
)


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def _structured(result: Any) -> dict[str, Any]:
    value = getattr(result, "structuredContent", None)
    if value is None:
        value = getattr(result, "structured_content", None)
    if isinstance(value, dict):
        return value
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise AssertionError("MCP tool returned no structured object")


async def _session(token: str, calls: list[tuple[str, dict[str, Any]]]):
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http_client:
        async with streamable_http_client(URL, http_client=http_client) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = sorted(tool.name for tool in (await session.list_tools()).tools)
                results = {}
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    results[name] = _structured(result)
                return tools, results


async def _oauth_token(username: str, password_secret: str) -> str:
    async with httpx2.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "client_secret": _secret("oauth_client_secret"),
                "username": username,
                "password": _secret(password_secret),
                "scope": REQUESTED_SCOPES,
            },
        )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    assert isinstance(token, str) and token
    return token


async def _revoke(token: str) -> int:
    async with httpx2.AsyncClient(timeout=20) as client:
        response = await client.post(
            REVOCATION_URL,
            data={
                "token": token,
                "token_type_hint": "access_token",
                "client_id": CLIENT_ID,
                "client_secret": _secret("oauth_client_secret"),
            },
        )
        response.raise_for_status()
        revoked = await client.get(
            URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
        )
    return revoked.status_code


def _odoo_acl_checks() -> dict[str, Any]:
    config = json.loads(_secret("odoo_config"))
    common = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/object")
    outcomes: dict[str, Any] = {}
    for label, instance_name in (
        ("full", "odoo16-staging-full"),
        ("crm", "odoo16-staging-crm"),
    ):
        instance = config["instances"][instance_name]
        uid = common.authenticate(
            instance["db"], instance["username"], instance["password"], {}
        )
        assert uid, f"Odoo authentication failed for {label}"
        lead_count = models.execute_kw(
            instance["db"], uid, instance["password"], "crm.lead", "search_count", [[]]
        )
        outcomes[f"{label}_lead_count"] = lead_count
        try:
            models.execute_kw(
                instance["db"],
                uid,
                instance["password"],
                "crm.lead",
                "create",
                [{"name": "WRITE MUST BE DENIED"}],
            )
        except xmlrpc.client.Fault:
            outcomes[f"{label}_write_denied"] = True
        else:
            raise AssertionError(f"Odoo write unexpectedly allowed for {label}")
        if label == "crm":
            try:
                models.execute_kw(
                    instance["db"],
                    uid,
                    instance["password"],
                    "sale.order",
                    "search_count",
                    [[]],
                )
            except xmlrpc.client.Fault:
                outcomes["crm_sale_acl_denied"] = True
            else:
                raise AssertionError("CRM-only Odoo user unexpectedly read sale.order")
    return outcomes


async def main() -> None:
    audit_path = Path("/var/log/odoo-mcp/audit.jsonl")
    audit_before = (
        len(audit_path.read_text(encoding="utf-8").splitlines())
        if audit_path.exists()
        else 0
    )
    async with httpx2.AsyncClient(timeout=10) as client:
        unauthenticated = await client.get(URL, headers={"Accept": "text/event-stream"})
    assert unauthenticated.status_code == 401

    full_token = await _oauth_token("staging-full-ai", "keycloak_full_password")
    crm_token = await _oauth_token("staging-crm-ai", "keycloak_crm_password")

    full_tools, full = await _session(
        full_token,
        [
            ("ngv_whoami", {}),
            ("ngv_list_leads", {"limit": 20}),
            ("ngv_list_quotations", {"limit": 20}),
            ("ngv_daily_sales_summary", {"timezone_name": "Asia/Ho_Chi_Minh"}),
        ],
    )
    crm_tools, crm = await _session(
        crm_token,
        [
            ("ngv_whoami", {}),
            ("ngv_list_leads", {"limit": 20}),
            ("ngv_list_quotations", {"limit": 20}),
        ],
    )
    assert len(full_tools) == 6 and full_tools == crm_tools
    assert full["ngv_whoami"]["success"] is True
    assert crm["ngv_whoami"]["success"] is True
    assert full["ngv_whoami"]["record_ids"] != crm["ngv_whoami"]["record_ids"]
    assert full["ngv_list_leads"]["count"] >= 2
    assert full["ngv_list_quotations"]["count"] >= 1
    assert crm["ngv_list_quotations"]["error_code"] == "access_denied"
    summary = full["ngv_daily_sales_summary"]
    assert summary["queried_at"] and summary["leads_today"]["record_ids"]
    assert summary["unprocessed_quotations"]["record_ids"]
    revoked_status = await _revoke(crm_token)
    assert revoked_status == 401

    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    audit_rows = [json.loads(line) for line in audit_lines[audit_before:]]
    assert any(row.get("outcome") == "success" for row in audit_rows)
    assert any(
        row.get("outcome") == "denied"
        and row.get("client_id") == CRM_IDENTITY
        for row in audit_rows
    )
    audit_text = audit_path.read_text(encoding="utf-8")
    assert full_token not in audit_text
    assert crm_token not in audit_text

    report = {
        "mcp_unauthenticated_status": unauthenticated.status_code,
        "revoked_token_status": revoked_status,
        "oauth_identities": [FULL_IDENTITY, CRM_IDENTITY],
        "tool_count": len(full_tools),
        "full_odoo_user_id": full["ngv_whoami"]["record_ids"][0],
        "crm_odoo_user_id": crm["ngv_whoami"]["record_ids"][0],
        "lead_count": full["ngv_list_leads"]["count"],
        "lead_record_ids": full["ngv_list_leads"]["record_ids"],
        "quotation_count": full["ngv_list_quotations"]["count"],
        "quotation_record_ids": full["ngv_list_quotations"]["record_ids"],
        "queried_at": summary["queried_at"],
        "crm_gateway_sale_denied": True,
        "audit_success_events": sum(
            row.get("outcome") == "success" for row in audit_rows
        ),
        "audit_denied_events": sum(
            row.get("outcome") == "denied" for row in audit_rows
        ),
        "odoo_acl": _odoo_acl_checks(),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
