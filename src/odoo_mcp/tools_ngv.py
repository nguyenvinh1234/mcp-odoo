"""Ngoc Viet business tools with a fixed, read-only Odoo surface."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver import Context

from .audit import record_gateway_event
from .ngv_adapters import ReadOnlyOdooAdapter, build_read_only_adapter
from .ngv_policy import ClientDecision, authorize_ngv_tool
from .server_core import READ_ONLY_TOOL, _resolve_odoo, mcp


MAX_PAGE_SIZE = 100
SUMMARY_RECORD_LIMIT = 200

LEAD_FIELDS = [
    "id",
    "name",
    "type",
    "stage_id",
    "user_id",
    "team_id",
    "create_date",
    "write_date",
    "active",
    "probability",
    "expected_revenue",
]
CUSTOMER_FIELDS = [
    "id",
    "name",
    "company_type",
    "customer_rank",
    "city",
    "country_id",
    "user_id",
    "active",
    "create_date",
]
QUOTATION_FIELDS = [
    "id",
    "name",
    "partner_id",
    "user_id",
    "team_id",
    "state",
    "date_order",
    "validity_date",
    "amount_total",
    "currency_id",
    "create_date",
]
PRODUCT_FIELDS = [
    "id",
    "name",
    "default_code",
    "type",
    "list_price",
    "currency_id",
    "sale_ok",
    "purchase_ok",
    "active",
    "create_date",
]


def _bounded_page(limit: int, offset: int) -> tuple[int, int]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if offset < 0:
        raise ValueError("offset must be greater than or equal to 0")
    return min(limit, MAX_PAGE_SIZE), offset


def _request_id(ctx: Context) -> str | None:
    try:
        return ctx.request_id
    except (AttributeError, ValueError):
        return None


def _adapter(ctx: Context, decision: ClientDecision) -> ReadOnlyOdooAdapter:
    resolved_instance, client = _resolve_odoo(ctx, decision.instance)
    if resolved_instance != decision.instance:
        raise PermissionError("Odoo instance mapping mismatch")
    adapter = build_read_only_adapter(client, decision.odoo_major)
    adapter.assert_compatible()
    return adapter


def _record_ids(result: dict[str, Any]) -> list[int]:
    records = result.get("records", [])
    if not isinstance(records, list):
        return []
    return [
        int(row["id"])
        for row in records
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    ]


def _execute(
    ctx: Context,
    tool: str,
    operation: Callable[[ReadOnlyOdooAdapter, ClientDecision], dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    decision: ClientDecision | None = None
    try:
        decision = authorize_ngv_tool(tool)
        result = operation(_adapter(ctx, decision), decision)
        record_ids = _record_ids(result)
        record_gateway_event(
            "ngv_query",
            outcome="success",
            client_id=decision.client_id,
            tool=tool,
            request_id=_request_id(ctx),
            instance=decision.instance,
            record_ids=record_ids,
            result_count=int(result.get("count", len(record_ids))),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result
    except PermissionError as exc:
        record_gateway_event(
            "ngv_query",
            outcome="denied",
            client_id=decision.client_id if decision else None,
            tool=tool,
            request_id=_request_id(ctx),
            instance=decision.instance if decision else None,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code="access_denied",
        )
        return {"success": False, "error": str(exc), "error_code": "access_denied"}
    except Exception as exc:  # noqa: BLE001 - return a bounded MCP error envelope
        record_gateway_event(
            "ngv_query",
            outcome="error",
            client_id=decision.client_id if decision else None,
            tool=tool,
            request_id=_request_id(ctx),
            instance=decision.instance if decision else None,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code="query_failed",
        )
        return {"success": False, "error": str(exc), "error_code": "query_failed"}


def _result(
    *,
    tool: str,
    decision: ClientDecision,
    records: list[dict[str, Any]],
    total: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    return {
        "success": True,
        "tool": tool,
        "gateway_client_id": decision.client_id,
        "odoo_instance": decision.instance,
        "odoo_major": decision.odoo_major,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "count": total,
        "returned": len(records),
        "limit": limit,
        "offset": offset,
        "records": records,
        "record_ids": [row["id"] for row in records],
    }


@mcp.tool(
    description="Show the gateway client and effective Odoo user without secrets",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def ngv_whoami(ctx: Context) -> dict[str, Any]:
    def operation(
        adapter: ReadOnlyOdooAdapter, decision: ClientDecision
    ) -> dict[str, Any]:
        user = adapter.whoami()
        return {
            "success": True,
            "tool": "ngv_whoami",
            "gateway_client_id": decision.client_id,
            "odoo_instance": decision.instance,
            "odoo_major": decision.odoo_major,
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "count": 1,
            "records": [user],
            "record_ids": [user["id"]],
        }

    return _execute(ctx, "ngv_whoami", operation)


@mcp.tool(
    description="List CRM leads/opportunities through fixed fields and Odoo ACLs",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def ngv_list_leads(
    ctx: Context,
    limit: int = 20,
    offset: int = 0,
    assigned_to_me: bool = False,
    created_within_days: int | None = None,
) -> dict[str, Any]:
    def operation(
        adapter: ReadOnlyOdooAdapter, decision: ClientDecision
    ) -> dict[str, Any]:
        bounded, page_offset = _bounded_page(limit, offset)
        domain: list[Any] = [["active", "=", True]]
        if assigned_to_me:
            domain.append(["user_id", "=", adapter.whoami()["id"]])
        if created_within_days is not None:
            if not 1 <= created_within_days <= 365:
                raise ValueError("created_within_days must be between 1 and 365")
            since = datetime.now(timezone.utc) - timedelta(days=created_within_days)
            domain.append(["create_date", ">=", since.strftime("%Y-%m-%d %H:%M:%S")])
        records = adapter.search(
            "crm.lead", domain, LEAD_FIELDS, limit=bounded, offset=page_offset
        )
        total = adapter.count("crm.lead", domain)
        return _result(
            tool="ngv_list_leads",
            decision=decision,
            records=records,
            total=total,
            limit=bounded,
            offset=page_offset,
        )

    return _execute(ctx, "ngv_list_leads", operation)


@mcp.tool(
    description="List customers through fixed non-contact fields and Odoo ACLs",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def ngv_list_customers(
    ctx: Context, limit: int = 20, offset: int = 0, name_query: str | None = None
) -> dict[str, Any]:
    def operation(
        adapter: ReadOnlyOdooAdapter, decision: ClientDecision
    ) -> dict[str, Any]:
        bounded, page_offset = _bounded_page(limit, offset)
        domain: list[Any] = [["active", "=", True], ["customer_rank", ">", 0]]
        if name_query and name_query.strip():
            domain.append(["name", "ilike", name_query.strip()[:100]])
        records = adapter.search(
            "res.partner", domain, CUSTOMER_FIELDS, limit=bounded, offset=page_offset
        )
        total = adapter.count("res.partner", domain)
        return _result(
            tool="ngv_list_customers",
            decision=decision,
            records=records,
            total=total,
            limit=bounded,
            offset=page_offset,
        )

    return _execute(ctx, "ngv_list_customers", operation)


@mcp.tool(
    description="List unprocessed quotations (draft/sent) through fixed fields",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def ngv_list_quotations(
    ctx: Context, limit: int = 20, offset: int = 0, assigned_to_me: bool = False
) -> dict[str, Any]:
    def operation(
        adapter: ReadOnlyOdooAdapter, decision: ClientDecision
    ) -> dict[str, Any]:
        bounded, page_offset = _bounded_page(limit, offset)
        domain: list[Any] = [["state", "in", ["draft", "sent"]]]
        if assigned_to_me:
            domain.append(["user_id", "=", adapter.whoami()["id"]])
        records = adapter.search(
            "sale.order", domain, QUOTATION_FIELDS, limit=bounded, offset=page_offset
        )
        total = adapter.count("sale.order", domain)
        return _result(
            tool="ngv_list_quotations",
            decision=decision,
            records=records,
            total=total,
            limit=bounded,
            offset=page_offset,
        )

    return _execute(ctx, "ngv_list_quotations", operation)


@mcp.tool(
    description="List active sale products through fixed fields and Odoo ACLs",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def ngv_list_products(
    ctx: Context, limit: int = 20, offset: int = 0, name_query: str | None = None
) -> dict[str, Any]:
    def operation(
        adapter: ReadOnlyOdooAdapter, decision: ClientDecision
    ) -> dict[str, Any]:
        bounded, page_offset = _bounded_page(limit, offset)
        domain: list[Any] = [["active", "=", True], ["sale_ok", "=", True]]
        if name_query and name_query.strip():
            domain.append(["name", "ilike", name_query.strip()[:100]])
        records = adapter.search(
            "product.template", domain, PRODUCT_FIELDS, limit=bounded, offset=page_offset
        )
        total = adapter.count("product.template", domain)
        return _result(
            tool="ngv_list_products",
            decision=decision,
            records=records,
            total=total,
            limit=bounded,
            offset=page_offset,
        )

    return _execute(ctx, "ngv_list_products", operation)


def _assignees(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        raw_user = row.get("user_id")
        if isinstance(raw_user, (list, tuple)) and len(raw_user) >= 2:
            user_id, name = raw_user[0], raw_user[1]
        else:
            user_id, name = None, "Chưa phân công"
        result.append(
            {
                "user_id": user_id,
                "name": name,
                "count": int(row.get("user_id_count", row.get("__count", 0))),
            }
        )
    return result


@mcp.tool(
    description=(
        "Count leads created today and unprocessed quotations, with assignees, "
        "record IDs, and query time"
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def ngv_daily_sales_summary(
    ctx: Context, timezone_name: str = "Asia/Ho_Chi_Minh"
) -> dict[str, Any]:
    def operation(
        adapter: ReadOnlyOdooAdapter, decision: ClientDecision
    ) -> dict[str, Any]:
        try:
            local_tz: tzinfo = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            if timezone_name == "Asia/Ho_Chi_Minh":
                # Vietnam has used UTC+07 without DST since 1975.  This
                # fallback keeps the default deterministic on minimal Windows
                # and container images that omit the IANA tzdata package.
                local_tz = timezone(timedelta(hours=7), timezone_name)
            else:
                raise ValueError(f"Unknown timezone: {timezone_name}") from exc
        now_utc = datetime.now(timezone.utc)
        local_now = now_utc.astimezone(local_tz)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        utc_start = local_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        utc_end = local_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        lead_domain: list[Any] = [
            ["active", "=", True],
            ["create_date", ">=", utc_start],
            ["create_date", "<", utc_end],
        ]
        quote_domain: list[Any] = [["state", "in", ["draft", "sent"]]]
        leads = adapter.search(
            "crm.lead",
            lead_domain,
            ["id", "name", "user_id", "create_date"],
            limit=SUMMARY_RECORD_LIMIT,
        )
        quotations = adapter.search(
            "sale.order",
            quote_domain,
            ["id", "name", "partner_id", "user_id", "state", "date_order"],
            limit=SUMMARY_RECORD_LIMIT,
        )
        lead_count = adapter.count("crm.lead", lead_domain)
        quotation_count = adapter.count("sale.order", quote_domain)
        return {
            "success": True,
            "tool": "ngv_daily_sales_summary",
            "gateway_client_id": decision.client_id,
            "odoo_instance": decision.instance,
            "odoo_major": decision.odoo_major,
            "queried_at": now_utc.isoformat(),
            "timezone": timezone_name,
            "definitions": {
                "leads_today": "active crm.lead records created during the local calendar day",
                "unprocessed_quotations": "sale.order records in draft or sent state",
            },
            "count": lead_count + quotation_count,
            "leads_today": {
                "count": lead_count,
                "record_ids": [row["id"] for row in leads],
                "records_truncated": lead_count > len(leads),
                "assignees": _assignees(
                    adapter.group_by_assignee("crm.lead", lead_domain)
                ),
            },
            "unprocessed_quotations": {
                "count": quotation_count,
                "record_ids": [row["id"] for row in quotations],
                "records_truncated": quotation_count > len(quotations),
                "assignees": _assignees(
                    adapter.group_by_assignee("sale.order", quote_domain)
                ),
            },
            "records": leads + quotations,
        }

    return _execute(ctx, "ngv_daily_sales_summary", operation)
