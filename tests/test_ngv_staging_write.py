import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.server.auth.provider import AccessToken

from odoo_mcp import ngv_policy, server_core, tools_ngv_write


WRITE_SCOPES = [
    "odoo.read",
    "odoo.crm.read",
    "odoo.customers.read",
    "odoo.sales.read",
    "odoo.products.read",
    "odoo.write.staging",
]


def _policy(tmp_path, *, client_id="writer-1"):
    path = tmp_path / "writer-policy.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "clients": {
                    client_id: {
                        "enabled": True,
                        "instance": "odoo16-staging-writer",
                        "odoo_major": 16,
                        "scopes": WRITE_SCOPES,
                        "tools": sorted(ngv_policy.NGV_STAGING_TOOLS),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _enable(monkeypatch, tmp_path, *, client_id="writer-1", scopes=None):
    monkeypatch.setenv("ODOO_MCP_SECURITY_PROFILE", "ngocviet-staging-write")
    monkeypatch.setenv(
        "ODOO_MCP_NGV_WRITE_STAGE_ACK", "I_UNDERSTAND_STAGING_ONLY"
    )
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_NGV_CLIENT_POLICY_FILE", str(_policy(tmp_path)))
    monkeypatch.setenv("ODOO_MCP_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    token = AccessToken(
        token="writer-secret-token",
        client_id=client_id,
        scopes=scopes or WRITE_SCOPES,
        expires_at=None,
        resource="https://mcp-write-staging.example/mcp",
    )
    monkeypatch.setattr(ngv_policy, "get_access_token", lambda: token)


class FakeWriteClient:
    transport = "xmlrpc"
    username = "mcp-writer@staging.invalid"

    def __init__(self):
        self.calls = []
        self.fail_execute = False

    def get_server_version(self):
        return {"server_version": "16.0"}

    def search_read(self, model, domain, fields=None, **kwargs):
        self.calls.append((model, "search_read", domain, fields, kwargs))
        if model == "crm.lead":
            return [
                {
                    "id": 21,
                    "name": "Writer lead",
                    "write_date": "2026-10-20 10:00:00",
                    "probability": 10.0,
                    "expected_revenue": 100.0,
                    "stage_id": [1, "New"],
                    "user_id": [8, "Writer"],
                }
            ]
        if model == "res.partner" and any(
            isinstance(item, list) and item[:2] == ["id", "="] for item in domain
        ):
            return [{"id": 11, "name": "Customer", "customer_rank": 1}]
        if model == "product.template":
            return [
                {
                    "id": 31,
                    "name": "CNC Product",
                    "product_variant_id": [41, "CNC Product"],
                    "list_price": 125.0,
                    "uom_id": [1, "Units"],
                }
            ]
        return []

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if self.fail_execute:
            raise TimeoutError("internal database address and secret")
        assert model == "ngv.mcp.request"
        assert method == "execute_request"
        action = args[0]
        model_by_action = {
            "customer.create": "res.partner",
            "lead.update": "crm.lead",
            "quotation.create_draft": "sale.order",
        }
        return {
            "model": model_by_action[action],
            "record_id": 901,
            "replayed": False,
            "state": "draft" if action == "quotation.create_draft" else None,
        }


class FakeCtx:
    def __init__(self, client):
        app = server_core.AppContext(odoo_factory=lambda: client)
        app._clients["odoo16-staging-writer"] = client
        self.request_context = SimpleNamespace(lifespan_context=app)


def test_staging_write_profile_filters_generic_write_tools(monkeypatch):
    registry = server_core.mcp._tool_manager._tools
    before = dict(registry)
    resource_manager = server_core.mcp._resource_manager
    resources_before = dict(resource_manager._resources)
    templates_before = dict(resource_manager._templates)
    prompts = server_core.mcp._prompt_manager._prompts
    prompts_before = dict(prompts)
    try:
        monkeypatch.setenv("ODOO_MCP_SECURITY_PROFILE", "ngocviet-staging-write")
        server_core.apply_tool_filter()
        names = {tool.name for tool in asyncio.run(server_core.mcp.list_tools())}
        assert names == ngv_policy.NGV_STAGING_TOOLS
        assert "execute_approved_write" not in names
        assert "execute_method" not in names
        assert server_core.mcp_surface_counts()["resource_count"] == 0
        assert server_core.mcp_surface_counts()["prompt_count"] == 0
    finally:
        registry.clear()
        registry.update(before)
        resource_manager._resources.update(resources_before)
        resource_manager._templates.update(templates_before)
        prompts.update(prompts_before)


def test_staging_write_startup_requires_ack_and_http(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    monkeypatch.delenv("ODOO_MCP_NGV_WRITE_STAGE_ACK")
    with pytest.raises(ValueError, match="requires ODOO_MCP_NGV_WRITE_STAGE_ACK"):
        ngv_policy.validate_ngv_startup()
    monkeypatch.setenv(
        "ODOO_MCP_NGV_WRITE_STAGE_ACK", "I_UNDERSTAND_STAGING_ONLY"
    )
    monkeypatch.setenv("ODOO_MCP_NGV_ALLOW_STDIO", "1")
    with pytest.raises(ValueError, match="authenticated HTTP"):
        ngv_policy.validate_ngv_startup()


def test_prepare_and_execute_customer_is_single_use_and_audited(
    monkeypatch, tmp_path
):
    _enable(monkeypatch, tmp_path)
    client = FakeWriteClient()
    ctx = FakeCtx(client)
    prepared = tools_ngv_write.ngv_prepare_customer_create(
        ctx,
        name="Staging Customer A",
        email="customer@example.test",
        idempotency_key="customer-0001",
    )
    assert prepared["success"] is True
    assert prepared["approval_required"] is True
    assert prepared["preview"]["has_email"] is True
    assert "customer@example.test" not in json.dumps(prepared)

    denied = tools_ngv_write.ngv_execute_approved_change(
        ctx, prepared["approval_token"], confirm=False
    )
    assert denied["error_code"] == "access_denied"
    assert not any(call[0] == "ngv.mcp.request" for call in client.calls)

    executed = tools_ngv_write.ngv_execute_approved_change(
        ctx, prepared["approval_token"], confirm=True
    )
    assert executed["success"] is True
    assert executed["record_id"] == 901

    replayed_token = tools_ngv_write.ngv_execute_approved_change(
        ctx, prepared["approval_token"], confirm=True
    )
    assert replayed_token["error_code"] == "access_denied"
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "writer-secret-token" not in audit
    assert "customer@example.test" not in audit
    assert "ngv_write_prepare" in audit
    assert "ngv_write_execute" in audit


def test_prepare_lead_requires_current_write_date(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    result = tools_ngv_write.ngv_prepare_lead_update(
        FakeCtx(FakeWriteClient()),
        lead_id=21,
        expected_write_date="2026-10-20 09:00:00",
        probability=50,
        idempotency_key="lead-0001",
    )
    assert result["success"] is False
    assert result["error_code"] == "validation_failed"


def test_prepare_draft_quotation_uses_server_read_product_price(
    monkeypatch, tmp_path
):
    _enable(monkeypatch, tmp_path)
    result = tools_ngv_write.ngv_prepare_draft_quotation(
        FakeCtx(FakeWriteClient()),
        partner_id=11,
        lines=[{"product_template_id": 31, "quantity": 2}],
        idempotency_key="quote-0001",
    )
    assert result["success"] is True
    assert result["preview"]["amount_estimate"] == 250.0
    assert result["preview"]["line_count"] == 1


def test_write_failure_is_sanitized_and_retryable(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    client = FakeWriteClient()
    ctx = FakeCtx(client)
    prepared = tools_ngv_write.ngv_prepare_customer_create(
        ctx,
        name="Staging Customer B",
        idempotency_key="customer-0002",
    )
    client.fail_execute = True
    result = tools_ngv_write.ngv_execute_approved_change(
        ctx, prepared["approval_token"], confirm=True
    )
    assert result["error_code"] == "write_failed"
    assert "internal database" not in json.dumps(result)
    assert prepared["approval_token"] in ctx.request_context.lifespan_context.ngv_write_approvals


def test_write_tool_denies_missing_write_scope_without_odoo_call(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, scopes=WRITE_SCOPES[:-1])
    client = FakeWriteClient()
    result = tools_ngv_write.ngv_prepare_customer_create(
        FakeCtx(client),
        name="Denied Customer",
        idempotency_key="customer-0003",
    )
    assert result["error_code"] == "access_denied"
    assert client.calls == []
