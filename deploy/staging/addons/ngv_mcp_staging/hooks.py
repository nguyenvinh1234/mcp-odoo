from pathlib import Path

from odoo import SUPERUSER_ID, api


def _secret(name):
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def _user(env, *, login, name, password, group):
    existing = env["res.users"].search([("login", "=", login)], limit=1)
    values = {
        "name": name,
        "login": login,
        "password": password,
        "active": True,
        "share": True,
        "groups_id": [(6, 0, [group.id])],
        "company_id": env.company.id,
        "company_ids": [(6, 0, [env.company.id])],
    }
    if existing:
        existing.write(values)
        return existing
    return env["res.users"].create(values)


def ensure_staging_writer(env):
    """Create/update only the controlled writer fixture, safe to run on upgrade."""
    writer_group = env.ref("ngv_mcp_staging.group_mcp_staging_writer")
    writer_user = _user(
        env,
        login="mcp-writer@staging.invalid",
        name="MCP Controlled Writer Staging",
        password=_secret("odoo_writer_password"),
        group=writer_group,
    )
    writer_lead = env["crm.lead"].search(
        [
            ("name", "=", "NGV staging lead writer"),
            ("user_id", "=", writer_user.id),
        ],
        limit=1,
    )
    if not writer_lead:
        customer = env["res.partner"].search(
            [("name", "=", "NGV Staging Customer")], limit=1
        )
        if not customer:
            customer = env["res.partner"].create(
                {
                    "name": "NGV Staging Customer",
                    "company_type": "company",
                    "customer_rank": 1,
                }
            )
        env["crm.lead"].create(
            {
                "name": "NGV staging lead writer",
                "type": "lead",
                "partner_id": customer.id,
                "user_id": writer_user.id,
            }
        )
    return writer_user


def post_init_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    crm_group = env.ref("ngv_mcp_staging.group_mcp_crm_readonly")
    full_group = env.ref("ngv_mcp_staging.group_mcp_full_readonly")
    full_user = _user(
        env,
        login="mcp-full@staging.invalid",
        name="MCP Full Readonly Staging",
        password=_secret("odoo_full_password"),
        group=full_group,
    )
    crm_user = _user(
        env,
        login="mcp-crm@staging.invalid",
        name="MCP CRM Readonly Staging",
        password=_secret("odoo_crm_password"),
        group=crm_group,
    )
    ensure_staging_writer(env)

    customer = env["res.partner"].create(
        {"name": "NGV Staging Customer", "company_type": "company", "customer_rank": 1}
    )
    env["crm.lead"].create(
        {
            "name": "NGV staging lead full",
            "type": "lead",
            "partner_id": customer.id,
            "user_id": full_user.id,
        }
    )
    env["crm.lead"].create(
        {
            "name": "NGV staging lead CRM",
            "type": "lead",
            "partner_id": customer.id,
            "user_id": crm_user.id,
        }
    )
    product = env["product.template"].create(
        {
            "name": "NGV CNC Staging Product",
            "default_code": "NGV-STAGING-001",
            "list_price": 1000000.0,
            "sale_ok": True,
        }
    )
    order = env["sale.order"].create(
        {
            "partner_id": customer.id,
            "user_id": full_user.id,
            "client_order_ref": "NGV-MCP-STAGING",
        }
    )
    env["sale.order.line"].create(
        {
            "order_id": order.id,
            "product_id": product.product_variant_id.id,
            "name": product.name,
            "product_uom_qty": 1.0,
            "price_unit": product.list_price,
        }
    )
