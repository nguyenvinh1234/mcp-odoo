import pytest

from scripts.odoo_compose_smoke import assert_tool_surface


NGV_TOOLS = {
    "ngv_whoami",
    "ngv_list_leads",
    "ngv_list_customers",
    "ngv_list_quotations",
    "ngv_list_products",
    "ngv_daily_sales_summary",
}

NGV_WRITE_TOOLS = {
    "ngv_prepare_customer_create",
    "ngv_prepare_lead_update",
    "ngv_prepare_draft_quotation",
    "ngv_execute_approved_change",
}


def test_inspector_accepts_exact_ngocviet_readonly_surface():
    assert_tool_surface(NGV_TOOLS, security_profile="ngocviet-readonly")


def test_inspector_rejects_full_tool_leak_in_ngocviet_readonly_surface():
    with pytest.raises(AssertionError, match="exactly six tools"):
        assert_tool_surface(
            NGV_TOOLS | {"execute_method"},
            security_profile="ngocviet-readonly",
        )


def test_inspector_accepts_exact_ngocviet_staging_write_surface():
    assert_tool_surface(
        NGV_TOOLS | NGV_WRITE_TOOLS,
        security_profile="ngocviet-staging-write",
    )


def test_inspector_rejects_generic_write_in_ngocviet_staging_write_surface():
    with pytest.raises(AssertionError, match="exactly ten tools"):
        assert_tool_surface(
            NGV_TOOLS | NGV_WRITE_TOOLS | {"execute_approved_write"},
            security_profile="ngocviet-staging-write",
        )


def test_inspector_rejects_unknown_security_profile():
    with pytest.raises(AssertionError, match="Unknown MCP security profile"):
        assert_tool_surface(NGV_TOOLS, security_profile="unexpected-profile")
