import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.server.auth.provider import AccessToken

from odoo_mcp import ngv_adapters, ngv_policy, server_core, tools_ngv


ALL_SCOPES = [
    "odoo.read",
    "odoo.crm.read",
    "odoo.customers.read",
    "odoo.sales.read",
    "odoo.products.read",
]


def _policy(tmp_path, *, enabled=True, tools=None):
    path = tmp_path / "clients.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "clients": {
                    "agent-1": {
                        "enabled": enabled,
                        "instance": "default",
                        "odoo_major": 16,
                        "scopes": ALL_SCOPES,
                        "tools": tools or sorted(ngv_policy.NGV_TOOLS),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _enable(monkeypatch, tmp_path, *, scopes=None, tools=None):
    monkeypatch.setenv("ODOO_MCP_SECURITY_PROFILE", "ngocviet-readonly")
    monkeypatch.setenv(
        "ODOO_MCP_NGV_CLIENT_POLICY_FILE", str(_policy(tmp_path, tools=tools))
    )
    monkeypatch.setenv("ODOO_MCP_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    token = AccessToken(
        token="secret-token",
        client_id="agent-1",
        scopes=scopes or ALL_SCOPES,
        expires_at=None,
        resource="https://mcp.example/mcp",
    )
    monkeypatch.setattr(ngv_policy, "get_access_token", lambda: token)


def test_policy_authorizes_exact_client_tool_and_scopes(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    decision = ngv_policy.authorize_ngv_tool("ngv_list_leads")
    assert decision.client_id == "agent-1"
    assert decision.instance == "default"
    assert decision.odoo_major == 16


def test_policy_denies_missing_token_scope(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, scopes=["odoo.read"])
    with pytest.raises(PermissionError, match="Bearer token"):
        ngv_policy.authorize_ngv_tool("ngv_list_leads")


def test_policy_revocation_applies_on_next_call(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ngv_policy.authorize_ngv_tool("ngv_whoami")
    _policy(tmp_path, enabled=False)
    with pytest.raises(PermissionError, match="disabled"):
        ngv_policy.authorize_ngv_tool("ngv_whoami")


def test_startup_refuses_write_escape_hatches(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    with pytest.raises(ValueError, match="refuses dangerous"):
        ngv_policy.validate_ngv_startup()


def test_ngv_profile_filters_every_non_business_tool(monkeypatch):
    registry = server_core.mcp._tool_manager._tools
    before = dict(registry)
    resource_manager = server_core.mcp._resource_manager
    resources_before = dict(resource_manager._resources)
    templates_before = dict(resource_manager._templates)
    prompts = server_core.mcp._prompt_manager._prompts
    prompts_before = dict(prompts)
    try:
        monkeypatch.setenv("ODOO_MCP_SECURITY_PROFILE", "ngocviet-readonly")
        server_core.apply_tool_filter()
        names = {tool.name for tool in asyncio.run(server_core.mcp.list_tools())}
        assert names == ngv_policy.NGV_TOOLS
        assert "execute_method" not in names
        assert "search_records" not in names
        assert server_core.mcp_surface_counts()["resource_count"] == 0
        assert server_core.mcp_surface_counts()["prompt_count"] == 0
    finally:
        registry.clear()
        registry.update(before)
        resource_manager._resources.update(resources_before)
        resource_manager._templates.update(templates_before)
        prompts.update(prompts_before)


class FakeClient:
    transport = "xmlrpc"
    username = "readonly-bot"

    def __init__(self):
        self.calls = []

    def get_server_version(self):
        return {"server_version": "16.0+e"}

    def search_read(self, model, domain, fields=None, **kwargs):
        self.calls.append((model, "search_read", domain, fields, kwargs))
        if model == "res.users":
            return [
                {
                    "id": 9,
                    "name": "Readonly Bot",
                    "active": True,
                    "company_id": [1, "Ngoc Viet"],
                    "company_ids": [1],
                }
            ]
        if model == "crm.lead":
            return [
                {
                    "id": 101,
                    "name": "Lead A",
                    "user_id": [9, "Readonly Bot"],
                    "create_date": "2026-10-19 01:00:00",
                }
            ]
        if model == "sale.order":
            return [
                {
                    "id": 202,
                    "name": "S0002",
                    "user_id": [9, "Readonly Bot"],
                    "state": "draft",
                }
            ]
        return []

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if method == "search_count":
            return 1
        if method == "read_group":
            return [{"user_id": [9, "Readonly Bot"], "user_id_count": 1}]
        raise AssertionError(method)


class FakeCtx:
    def __init__(self, client):
        life = SimpleNamespace(
            get_client=lambda instance=None: (instance or "default", client)
        )
        self.request_context = SimpleNamespace(lifespan_context=life)


def test_odoo16_adapter_rejects_wrong_transport():
    client = FakeClient()
    client.transport = "json2"
    with pytest.raises(ValueError, match="requires xmlrpc"):
        ngv_adapters.Odoo16ReadOnlyAdapter(client).assert_compatible()


def test_daily_summary_has_real_ids_timestamp_and_fixed_queries(
    monkeypatch, tmp_path
):
    _enable(monkeypatch, tmp_path)
    client = FakeClient()
    result = tools_ngv.ngv_daily_sales_summary(FakeCtx(client))
    assert result["success"] is True
    assert result["leads_today"]["record_ids"] == [101]
    assert result["unprocessed_quotations"]["record_ids"] == [202]
    assert result["queried_at"].endswith("+00:00")
    assert all(
        call[1] in {"search_read", "search_count", "read_group"}
        for call in client.calls
    )
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "secret-token" not in audit_text
    assert "ngv_daily_sales_summary" in audit_text


def test_lead_tool_denial_never_reaches_odoo(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, scopes=["odoo.read"])
    client = FakeClient()
    result = tools_ngv.ngv_list_leads(FakeCtx(client))
    assert result["success"] is False
    assert result["error_code"] == "access_denied"
    assert result["error"] == tools_ngv.ACCESS_DENIED_MESSAGE
    assert "Bearer token" not in json.dumps(result)
    assert client.calls == []


def test_query_failure_never_exposes_internal_exception(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    client = FakeClient()
    internal_detail = "postgresql://internal-user:secret@db/production"

    def fail_server_version():
        raise RuntimeError(internal_detail)

    client.get_server_version = fail_server_version
    result = tools_ngv.ngv_list_leads(FakeCtx(client))

    assert result["success"] is False
    assert result["error_code"] == "query_failed"
    assert result["error"] == tools_ngv.QUERY_FAILED_MESSAGE
    assert internal_detail not in json.dumps(result)
    assert internal_detail not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
