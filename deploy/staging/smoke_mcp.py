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
    async with streamable_http_client(URL, headers=headers) as streams:
        read_stream, write_stream = streams[0], streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = sorted(tool.name for tool in (await session.list_tools()).tools)
            results = {}
            for name, arguments in calls:
                results[name] = _structured(await session.call_tool(name, arguments))
            return tools, results


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
    async with httpx2.AsyncClient(timeout=10) as client:
        unauthenticated = await client.get(URL, headers={"Accept": "text/event-stream"})
    assert unauthenticated.status_code == 401

    full_tools, full = await _session(
        _secret("oauth_full_token"),
        [
            ("ngv_whoami", {}),
            ("ngv_list_leads", {"limit": 20}),
            ("ngv_list_quotations", {"limit": 20}),
            ("ngv_daily_sales_summary", {"timezone_name": "Asia/Ho_Chi_Minh"}),
        ],
    )
    crm_tools, crm = await _session(
        _secret("oauth_crm_token"),
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

    audit_path = Path("/var/log/odoo-mcp/audit.jsonl")
    audit_rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert any(row.get("outcome") == "success" for row in audit_rows)
    assert any(row.get("outcome") == "denied" for row in audit_rows)
    audit_text = audit_path.read_text(encoding="utf-8")
    assert _secret("oauth_full_token") not in audit_text
    assert _secret("oauth_crm_token") not in audit_text

    report = {
        "mcp_unauthenticated_status": unauthenticated.status_code,
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
