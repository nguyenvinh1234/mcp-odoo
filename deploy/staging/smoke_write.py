"""E2E proof for controlled writes against the isolated Odoo 16 staging stack."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


URL = os.environ.get("MCP_WRITE_TEST_URL", "http://gateway-write:8000/mcp")
TOKEN_URL = os.environ["MCP_AUTH_TOKEN_URL"]
REVOCATION_URL = os.environ["MCP_AUTH_REVOCATION_URL"]
CLIENT_ID = "ngv-odoo-mcp-staging"
READ_SCOPES = {
    "odoo.read",
    "odoo.crm.read",
    "odoo.customers.read",
    "odoo.sales.read",
    "odoo.products.read",
}
WRITE_SCOPES = READ_SCOPES | {"odoo.write.staging"}


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
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError("MCP tool returned no structured object")


async def _oauth_token(username: str, password_secret: str, scopes: set[str]) -> str:
    async with httpx2.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "client_secret": _secret("oauth_client_secret"),
                "username": username,
                "password": _secret(password_secret),
                "scope": " ".join(sorted(scopes)),
            },
        )
    response.raise_for_status()
    token = response.json().get("access_token")
    assert isinstance(token, str) and token
    return token


async def _mcp_calls(token: str, calls: list[tuple[str, dict[str, Any]]]):
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=45) as http_client:
        async with streamable_http_client(URL, http_client=http_client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = sorted(tool.name for tool in (await session.list_tools()).tools)
                results = []
                for name, arguments in calls:
                    results.append(_structured(await session.call_tool(name, arguments)))
                return tools, results


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
        denied = await client.get(
            URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        )
    return denied.status_code


def _rpc_config() -> tuple[dict[str, Any], dict[str, Any]]:
    config = json.loads(_secret("odoo_config"))
    return config, config["instances"]["odoo16-staging-writer"]


def _rpc_call(action: str, request_key: str, payload: dict[str, Any], *, rollback=False):
    _config, instance = _rpc_config()
    common = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/object")
    uid = common.authenticate(
        instance["db"], instance["username"], instance["password"], {}
    )
    assert uid
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return models.execute_kw(
        instance["db"],
        uid,
        instance["password"],
        "ngv.mcp.request",
        "execute_request",
        [
            action,
            request_key,
            payload_hash,
            hashlib.sha256(b"direct-staging-smoke").hexdigest(),
            payload,
        ],
        {"context": {"ngv_mcp_test_rollback": True}} if rollback else {},
    )


def _rpc_count(model: str, domain: list[Any]) -> int:
    _config, instance = _rpc_config()
    common = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/object")
    uid = common.authenticate(
        instance["db"], instance["username"], instance["password"], {}
    )
    assert uid
    return int(
        models.execute_kw(
            instance["db"],
            uid,
            instance["password"],
            model,
            "search_count",
            [domain],
        )
    )


def _direct_write_denied() -> bool:
    _config, instance = _rpc_config()
    common = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy("http://odoo16:8069/xmlrpc/2/object")
    uid = common.authenticate(
        instance["db"], instance["username"], instance["password"], {}
    )
    try:
        models.execute_kw(
            instance["db"],
            uid,
            instance["password"],
            "res.partner",
            "create",
            [{"name": "DIRECT WRITE MUST BE DENIED"}],
        )
    except xmlrpc.client.Fault:
        return True
    return False


def _rollback_probe(suffix: str) -> bool:
    name = f"NGV rollback probe {suffix}"
    request_key = hashlib.sha256(f"rollback:{suffix}".encode()).hexdigest()
    payload = {
        "name": name,
        "company_type": "company",
        "email": None,
        "phone": None,
        "vat": None,
    }
    try:
        _rpc_call("customer.create", request_key, payload, rollback=True)
    except xmlrpc.client.Fault:
        pass
    else:
        return False
    return _rpc_count("res.partner", [["name", "=", name]]) == 0 and _rpc_count(
        "ngv.mcp.request", [["request_key", "=", request_key]]
    ) == 0


def _concurrency_probe(suffix: str) -> dict[str, Any]:
    name = f"NGV concurrency probe {suffix}"
    request_key = hashlib.sha256(f"concurrent:{suffix}".encode()).hexdigest()
    payload = {
        "name": name,
        "company_type": "company",
        "email": None,
        "phone": None,
        "vat": None,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: _rpc_call("customer.create", request_key, payload),
                range(2),
            )
        )
    ids = {int(result["record_id"]) for result in results}
    return {
        "one_record": len(ids) == 1 and _rpc_count("res.partner", [["name", "=", name]]) == 1,
        "one_replay": sum(bool(result.get("replayed")) for result in results) == 1,
        "record_id": next(iter(ids)),
    }


async def main() -> None:
    suffix = uuid.uuid4().hex[:10]
    audit_path = Path("/var/log/odoo-mcp/audit.jsonl")
    audit_before = len(audit_path.read_text(encoding="utf-8").splitlines())
    async with httpx2.AsyncClient(timeout=10) as client:
        unauthenticated = await client.get(URL, headers={"Accept": "text/event-stream"})
    assert unauthenticated.status_code == 401

    writer_token = await _oauth_token(
        "staging-writer-ai", "keycloak_writer_password", WRITE_SCOPES
    )
    full_token = await _oauth_token(
        "staging-full-ai", "keycloak_full_password", WRITE_SCOPES
    )

    tools, reads = await _mcp_calls(
        writer_token,
        [("ngv_whoami", {}), ("ngv_list_leads", {"limit": 100}), ("ngv_list_products", {"limit": 10})],
    )
    assert len(tools) == 10 and set(tools) == {
        "ngv_whoami",
        "ngv_list_leads",
        "ngv_list_customers",
        "ngv_list_quotations",
        "ngv_list_products",
        "ngv_daily_sales_summary",
        "ngv_prepare_customer_create",
        "ngv_prepare_lead_update",
        "ngv_prepare_draft_quotation",
        "ngv_execute_approved_change",
    }
    whoami, leads, products = reads
    assert whoami["success"] and leads["records"] and products["records"]

    _, denied_results = await _mcp_calls(
        full_token,
        [
            (
                "ngv_prepare_customer_create",
                {"name": "MUST NOT WRITE", "idempotency_key": f"denied-{suffix}"},
            )
        ],
    )
    assert denied_results[0]["error_code"] == "access_denied"

    customer_name = f"NGV MCP E2E customer {suffix}"
    customer_email = f"mcp-e2e-{suffix}@staging.invalid"
    _, customer_prepared_rows = await _mcp_calls(
        writer_token,
        [
            (
                "ngv_prepare_customer_create",
                {
                    "name": customer_name,
                    "email": customer_email,
                    "idempotency_key": f"customer-{suffix}",
                },
            )
        ],
    )
    customer_prepared = customer_prepared_rows[0]
    assert customer_prepared["success"]
    _, customer_executed_rows = await _mcp_calls(
        writer_token,
        [
            (
                "ngv_execute_approved_change",
                {"approval_token": customer_prepared["approval_token"], "confirm": True},
            )
        ],
    )
    customer = customer_executed_rows[0]
    assert customer["success"] and customer["model"] == "res.partner"

    writer_lead = next(
        row for row in leads["records"] if row.get("write_date") and row.get("user_id")
    )
    new_probability = 37.0 if float(writer_lead.get("probability") or 0) != 37.0 else 38.0
    _, lead_prepared_rows = await _mcp_calls(
        writer_token,
        [
            (
                "ngv_prepare_lead_update",
                {
                    "lead_id": writer_lead["id"],
                    "expected_write_date": writer_lead["write_date"],
                    "probability": new_probability,
                    "idempotency_key": f"lead-{suffix}",
                },
            )
        ],
    )
    _, lead_executed_rows = await _mcp_calls(
        writer_token,
        [
            (
                "ngv_execute_approved_change",
                {"approval_token": lead_prepared_rows[0]["approval_token"], "confirm": True},
            )
        ],
    )
    lead_result = lead_executed_rows[0]
    assert lead_result["success"] and lead_result["model"] == "crm.lead"

    product_id = products["records"][0]["id"]
    quote_arguments = {
        "partner_id": customer["record_id"],
        "lines": [{"product_template_id": product_id, "quantity": 2}],
        "client_order_ref": f"NGV-MCP-{suffix}",
        "idempotency_key": f"quote-{suffix}",
    }
    _, quote_prepared_rows = await _mcp_calls(
        writer_token, [("ngv_prepare_draft_quotation", quote_arguments)]
    )
    _, quote_executed_rows = await _mcp_calls(
        writer_token,
        [
            (
                "ngv_execute_approved_change",
                {"approval_token": quote_prepared_rows[0]["approval_token"], "confirm": True},
            )
        ],
    )
    quote = quote_executed_rows[0]
    assert quote["success"] and quote["state"] == "draft"

    _, quote_reprepared_rows = await _mcp_calls(
        writer_token, [("ngv_prepare_draft_quotation", quote_arguments)]
    )
    _, quote_replayed_rows = await _mcp_calls(
        writer_token,
        [
            (
                "ngv_execute_approved_change",
                {"approval_token": quote_reprepared_rows[0]["approval_token"], "confirm": True},
            )
        ],
    )
    quote_replay = quote_replayed_rows[0]
    assert quote_replay["record_id"] == quote["record_id"]
    assert quote_replay["idempotent_replay"] is True

    assert _direct_write_denied()
    assert _rpc_count(
        "res.partner", [["name", "=", "NGV MCP CROSS COMPANY MUST STAY HIDDEN"]]
    ) == 0
    rollback_ok = _rollback_probe(suffix)
    concurrency = _concurrency_probe(suffix)
    assert rollback_ok and concurrency["one_record"] and concurrency["one_replay"]

    revoked_status = await _revoke(writer_token)
    assert revoked_status == 401
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()[audit_before:]
    audit_text = "\n".join(audit_lines)
    assert writer_token not in audit_text and full_token not in audit_text
    assert customer_email not in audit_text and customer_name not in audit_text
    audit_rows = [json.loads(line) for line in audit_lines]

    report = {
        "mcp_unauthenticated_status": unauthenticated.status_code,
        "revoked_token_status": revoked_status,
        "tool_count": len(tools),
        "writer_odoo_user_id": whoami["record_ids"][0],
        "customer_record_id": customer["record_id"],
        "lead_record_id": lead_result["record_id"],
        "quotation_record_id": quote["record_id"],
        "quotation_state": quote["state"],
        "idempotent_replay": quote_replay["idempotent_replay"],
        "cross_client_denied": True,
        "cross_company_hidden": True,
        "direct_write_denied": True,
        "rollback_passed": rollback_ok,
        "concurrent_same_key_one_record": concurrency["one_record"],
        "concurrent_same_key_one_replay": concurrency["one_replay"],
        "audit_success_events": sum(row.get("outcome") == "success" for row in audit_rows),
        "audit_denied_events": sum(row.get("outcome") == "denied" for row in audit_rows),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
