"""Refresh date-sensitive records in the isolated Odoo staging database.

This file is executed by ``odoo shell`` through the ``fixture-refresh``
Compose profile. It is intentionally unavailable to the MCP gateway and is
idempotent for each Asia/Ho_Chi_Minh calendar day.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


local_zone = ZoneInfo("Asia/Ho_Chi_Minh")
local_today = datetime.now(local_zone).date()
utc_start = datetime.combine(local_today, time.min, local_zone).astimezone(timezone.utc)
utc_end = datetime.combine(
    local_today + timedelta(days=1), time.min, local_zone
).astimezone(timezone.utc)
start_text = utc_start.strftime("%Y-%m-%d %H:%M:%S")
end_text = utc_end.strftime("%Y-%m-%d %H:%M:%S")
marker = local_today.isoformat()

full_user = env["res.users"].search(  # noqa: F821 - provided by odoo shell
    [("login", "=", "mcp-full@staging.invalid")], limit=1
)
customer = env["res.partner"].search(  # noqa: F821 - provided by odoo shell
    [("name", "=", "NGV Staging Customer")], limit=1
)
product = env["product.product"].search(  # noqa: F821 - provided by odoo shell
    [("default_code", "=", "NGV-STAGING-001")], limit=1
)
if not full_user or not customer or not product:
    raise RuntimeError("Base staging fixtures are missing; rerun odoo16-init")

lead_domain = [
    ("name", "=", f"NGV staging daily lead {marker}"),
    ("create_date", ">=", start_text),
    ("create_date", "<", end_text),
]
lead = env["crm.lead"].search(lead_domain, limit=1)  # noqa: F821
if not lead:
    lead = env["crm.lead"].create(  # noqa: F821
        {
            "name": f"NGV staging daily lead {marker}",
            "type": "lead",
            "partner_id": customer.id,
            "user_id": full_user.id,
        }
    )

quotation = env["sale.order"].search(  # noqa: F821
    [("client_order_ref", "=", f"NGV-MCP-STAGING-{marker}")], limit=1
)
if not quotation:
    quotation = env["sale.order"].create(  # noqa: F821
        {
            "partner_id": customer.id,
            "user_id": full_user.id,
            "client_order_ref": f"NGV-MCP-STAGING-{marker}",
        }
    )
    env["sale.order.line"].create(  # noqa: F821
        {
            "order_id": quotation.id,
            "product_id": product.id,
            "name": product.name,
            "product_uom_qty": 1.0,
            "price_unit": product.lst_price,
        }
    )

env.cr.commit()  # noqa: F821
print(
    "STAGING_FIXTURE_PASS "
    f"date={marker} lead_id={lead.id} quotation_id={quotation.id}"
)
