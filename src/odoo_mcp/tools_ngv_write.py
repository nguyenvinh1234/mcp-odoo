"""Narrow, approval-gated write tools for the isolated Ngoc Viet staging stack."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, cast

from mcp.server.mcpserver import Context

from .agent_tools import canonical_json
from .audit import record_gateway_event
from .ngv_adapters import Odoo16StagingWriteAdapter, build_staging_write_adapter
from .ngv_policy import ClientDecision, authorize_ngv_tool, current_request_client_id
from .server_core import AppContext, DESTRUCTIVE_TOOL, PREVIEW_TOOL, _resolve_odoo, mcp


APPROVAL_TTL_SECONDS = 10 * 60
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
MAX_QUOTATION_LINES = 20
WRITE_FAILED_MESSAGE = (
    "The approved staging change failed. Use request_id and audit logs for support."
)


def _request_id(ctx: Context) -> str | None:
    try:
        return ctx.request_id
    except (AttributeError, ValueError):
        return None


def _app_context(ctx: Context) -> AppContext:
    return cast(AppContext, ctx.request_context.lifespan_context)


def _adapter(ctx: Context, decision: ClientDecision) -> Odoo16StagingWriteAdapter:
    resolved_instance, client = _resolve_odoo(ctx, decision.instance)
    if resolved_instance != decision.instance:
        raise PermissionError("Odoo instance mapping mismatch")
    adapter = build_staging_write_adapter(client, decision.odoo_major)
    adapter.assert_compatible()
    return adapter


def _required_text(value: str, field: str, *, maximum: int) -> str:
    normalized = " ".join(value.split()).strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _optional_text(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not IDEMPOTENCY_RE.fullmatch(normalized):
        raise ValueError(
            "idempotency_key must be 8-64 characters using letters, numbers, . _ : -"
        )
    return normalized


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _client_digest(client_id: str) -> str:
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()


def _internal_request_key(client_id: str, idempotency_key: str) -> str:
    material = f"{client_id}:{idempotency_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _store_approval(
    ctx: Context,
    decision: ClientDecision,
    *,
    tool: str,
    action: str,
    idempotency_key: str,
    payload: dict[str, Any],
    preview: dict[str, Any],
    record_ids: list[int] | None = None,
) -> dict[str, Any]:
    app = _app_context(ctx)
    token = f"ngv-stage:{secrets.token_urlsafe(24)}"
    now = time.time()
    record = {
        "token": token,
        "client_id": decision.client_id,
        "instance": decision.instance,
        "action": action,
        "request_key": _internal_request_key(decision.client_id, idempotency_key),
        "payload_hash": _payload_hash(payload),
        "client_digest": _client_digest(decision.client_id),
        "payload": payload,
        "created_at": now,
        "expires_at": now + APPROVAL_TTL_SECONDS,
    }
    with app.ngv_write_lock:
        expired = [
            key
            for key, value in app.ngv_write_approvals.items()
            if now > float(value.get("expires_at", 0))
        ]
        for key in expired:
            app.ngv_write_approvals.pop(key, None)
        app.ngv_write_approvals[token] = record
    audited = record_gateway_event(
        "ngv_write_prepare",
        outcome="approval_required",
        client_id=decision.client_id,
        tool=tool,
        request_id=_request_id(ctx),
        instance=decision.instance,
        record_ids=record_ids,
        result_count=len(record_ids or []),
    )
    if not audited:
        with app.ngv_write_lock:
            app.ngv_write_approvals.pop(token, None)
        raise RuntimeError("Audit log is unavailable; refusing to prepare a write")
    return {
        "success": True,
        "tool": tool,
        "action": action,
        "approval_required": True,
        "approval_token": token,
        "expires_at": datetime.fromtimestamp(
            now + APPROVAL_TTL_SECONDS, timezone.utc
        ).isoformat(),
        "idempotency_key": idempotency_key,
        "preview": preview,
        "instruction": (
            "A human reviewer must inspect this preview, then call "
            "ngv_execute_approved_change with approval_token and confirm=true."
        ),
    }


def _prepare_error(ctx: Context, tool: str, exc: Exception) -> dict[str, Any]:
    code = "access_denied" if isinstance(exc, PermissionError) else "validation_failed"
    record_gateway_event(
        "ngv_write_prepare",
        outcome="denied" if code == "access_denied" else "error",
        client_id=current_request_client_id(),
        tool=tool,
        request_id=_request_id(ctx),
        error_code=code,
    )
    message = (
        "Access denied by gateway policy."
        if code == "access_denied"
        else "The requested staging change did not pass validation."
    )
    return {
        "success": False,
        "tool": tool,
        "error": message,
        "error_code": code,
        "request_id": _request_id(ctx),
    }


@mcp.tool(
    description="Prepare creation of one staging customer; never writes before approval",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def ngv_prepare_customer_create(
    ctx: Context,
    name: str,
    idempotency_key: str,
    company_type: str = "company",
    email: str | None = None,
    phone: str | None = None,
    vat: str | None = None,
) -> dict[str, Any]:
    tool = "ngv_prepare_customer_create"
    try:
        decision = authorize_ngv_tool(tool)
        adapter = _adapter(ctx, decision)
        normalized_name = _required_text(name, "name", maximum=120)
        normalized_type = company_type.strip().lower()
        if normalized_type not in {"company", "person"}:
            raise ValueError("company_type must be company or person")
        normalized_email = _optional_text(email, "email", maximum=254)
        normalized_phone = _optional_text(phone, "phone", maximum=40)
        normalized_vat = _optional_text(vat, "vat", maximum=40)
        if normalized_email and "@" not in normalized_email:
            raise ValueError("email is invalid")
        key = _idempotency_key(idempotency_key)
        duplicate_domain: list[Any] = [["name", "=ilike", normalized_name]]
        if normalized_email:
            duplicate_domain = [
                "|",
                ["name", "=ilike", normalized_name],
                ["email", "=ilike", normalized_email],
            ]
        if adapter.search(
            "res.partner", duplicate_domain, ["id", "name"], limit=1
        ):
            raise ValueError("possible duplicate customer")
        payload = {
            "name": normalized_name,
            "company_type": normalized_type,
            "email": normalized_email,
            "phone": normalized_phone,
            "vat": normalized_vat,
        }
        return _store_approval(
            ctx,
            decision,
            tool=tool,
            action="customer.create",
            idempotency_key=key,
            payload=payload,
            preview={
                "model": "res.partner",
                "operation": "create",
                "name": normalized_name,
                "company_type": normalized_type,
                "has_email": normalized_email is not None,
                "has_phone": normalized_phone is not None,
                "has_vat": normalized_vat is not None,
            },
        )
    except Exception as exc:  # noqa: BLE001 - bounded error envelope
        return _prepare_error(ctx, tool, exc)


@mcp.tool(
    description="Prepare a constrained staging CRM lead update with optimistic locking",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def ngv_prepare_lead_update(
    ctx: Context,
    lead_id: int,
    expected_write_date: str,
    idempotency_key: str,
    name: str | None = None,
    probability: float | None = None,
    expected_revenue: float | None = None,
    stage_id: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    tool = "ngv_prepare_lead_update"
    try:
        decision = authorize_ngv_tool(tool)
        adapter = _adapter(ctx, decision)
        if lead_id < 1:
            raise ValueError("lead_id must be positive")
        current = adapter.search(
            "crm.lead",
            [["id", "=", lead_id]],
            ["id", "name", "write_date", "probability", "expected_revenue", "stage_id", "user_id"],
            limit=1,
        )
        if not current:
            raise PermissionError("lead is missing or outside Odoo record rules")
        expected = _required_text(expected_write_date, "expected_write_date", maximum=40)
        if str(current[0].get("write_date") or "") != expected:
            raise ValueError("lead changed since it was read")
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = _required_text(name, "name", maximum=200)
        if probability is not None:
            if probability < 0 or probability > 100:
                raise ValueError("probability must be between 0 and 100")
            values["probability"] = float(probability)
        if expected_revenue is not None:
            if expected_revenue < 0 or expected_revenue > 1_000_000_000_000:
                raise ValueError("expected_revenue is outside the supported range")
            values["expected_revenue"] = float(expected_revenue)
        if stage_id is not None:
            if stage_id < 1:
                raise ValueError("stage_id must be positive")
            values["stage_id"] = int(stage_id)
        if user_id is not None:
            if user_id < 1:
                raise ValueError("user_id must be positive")
            values["user_id"] = int(user_id)
        if not values:
            raise ValueError("at least one lead field must be changed")
        payload = {
            "lead_id": int(lead_id),
            "expected_write_date": expected,
            "values": values,
        }
        return _store_approval(
            ctx,
            decision,
            tool=tool,
            action="lead.update",
            idempotency_key=_idempotency_key(idempotency_key),
            payload=payload,
            preview={
                "model": "crm.lead",
                "operation": "write",
                "record_id": int(lead_id),
                "changed_fields": sorted(values),
                "current_write_date": expected,
            },
            record_ids=[lead_id],
        )
    except Exception as exc:  # noqa: BLE001 - bounded error envelope
        return _prepare_error(ctx, tool, exc)


@mcp.tool(
    description="Prepare one staging draft quotation using fixed product and quantity fields",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def ngv_prepare_draft_quotation(
    ctx: Context,
    partner_id: int,
    lines: list[dict[str, Any]],
    idempotency_key: str,
    client_order_ref: str | None = None,
) -> dict[str, Any]:
    tool = "ngv_prepare_draft_quotation"
    try:
        decision = authorize_ngv_tool(tool)
        adapter = _adapter(ctx, decision)
        if partner_id < 1:
            raise ValueError("partner_id must be positive")
        if not 1 <= len(lines) <= MAX_QUOTATION_LINES:
            raise ValueError(f"quotation requires 1-{MAX_QUOTATION_LINES} lines")
        partner = adapter.search(
            "res.partner",
            [["id", "=", partner_id], ["active", "=", True]],
            ["id", "name", "customer_rank"],
            limit=1,
        )
        if not partner:
            raise PermissionError("customer is missing or outside Odoo record rules")
        normalized_lines: list[dict[str, Any]] = []
        seen_templates: set[int] = set()
        for line in lines:
            template_id = int(line.get("product_template_id") or 0)
            quantity = float(line.get("quantity") or 0)
            if template_id < 1 or quantity <= 0 or quantity > 10000:
                raise ValueError("each line requires a valid product_template_id and quantity")
            if template_id in seen_templates:
                raise ValueError("duplicate product_template_id in quotation lines")
            seen_templates.add(template_id)
            products = adapter.search(
                "product.template",
                [["id", "=", template_id], ["active", "=", True], ["sale_ok", "=", True]],
                ["id", "name", "product_variant_id", "list_price", "uom_id"],
                limit=1,
            )
            if not products:
                raise PermissionError("product is missing or outside Odoo record rules")
            product = products[0]
            variant = product.get("product_variant_id")
            uom = product.get("uom_id")
            if not isinstance(variant, (list, tuple)) or not variant:
                raise ValueError("product variant is unavailable")
            if not isinstance(uom, (list, tuple)) or not uom:
                raise ValueError("product unit of measure is unavailable")
            normalized_lines.append(
                {
                    "product_template_id": template_id,
                    "product_id": int(variant[0]),
                    "name": str(product.get("name") or "Product"),
                    "product_uom": int(uom[0]),
                    "quantity": quantity,
                    "price_unit": float(product.get("list_price") or 0),
                }
            )
        reference = _optional_text(client_order_ref, "client_order_ref", maximum=120)
        payload = {
            "partner_id": int(partner_id),
            "client_order_ref": reference,
            "lines": normalized_lines,
        }
        return _store_approval(
            ctx,
            decision,
            tool=tool,
            action="quotation.create_draft",
            idempotency_key=_idempotency_key(idempotency_key),
            payload=payload,
            preview={
                "model": "sale.order",
                "operation": "create_draft",
                "partner_id": int(partner_id),
                "line_count": len(normalized_lines),
                "product_template_ids": sorted(seen_templates),
                "amount_estimate": sum(
                    line["quantity"] * line["price_unit"] for line in normalized_lines
                ),
            },
            record_ids=[partner_id],
        )
    except Exception as exc:  # noqa: BLE001 - bounded error envelope
        return _prepare_error(ctx, tool, exc)


@mcp.tool(
    description="Execute one exact, unexpired, human-approved staging change",
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
def ngv_execute_approved_change(
    ctx: Context,
    approval_token: str,
    confirm: bool = False,
) -> dict[str, Any]:
    tool = "ngv_execute_approved_change"
    decision: ClientDecision | None = None
    try:
        decision = authorize_ngv_tool(tool)
        if confirm is not True:
            raise PermissionError("human confirmation is required")
        app = _app_context(ctx)
        with app.ngv_write_lock:
            approval = app.ngv_write_approvals.get(approval_token)
            if approval is None:
                raise PermissionError("approval is missing, expired, or already used")
            if time.time() > float(approval.get("expires_at", 0)):
                app.ngv_write_approvals.pop(approval_token, None)
                raise PermissionError("approval expired")
            if approval.get("client_id") != decision.client_id:
                raise PermissionError("approval belongs to a different client")
            if approval.get("instance") != decision.instance:
                raise PermissionError("approval belongs to a different instance")
            audited = record_gateway_event(
                "ngv_write_execute",
                outcome="approved",
                client_id=decision.client_id,
                tool=tool,
                request_id=_request_id(ctx),
                instance=decision.instance,
            )
            if not audited:
                raise RuntimeError("Audit log is unavailable; refusing the write")
            adapter = _adapter(ctx, decision)
            result = adapter.execute_request(
                action=str(approval["action"]),
                request_key=str(approval["request_key"]),
                payload_hash=str(approval["payload_hash"]),
                client_digest=str(approval["client_digest"]),
                payload=dict(approval["payload"]),
            )
            record_id = int(result.get("record_id") or 0)
            if record_id < 1:
                raise ValueError("staging writer did not return a record ID")
            app.ngv_write_approvals.pop(approval_token, None)
        record_gateway_event(
            "ngv_write_execute",
            outcome="success",
            client_id=decision.client_id,
            tool=tool,
            request_id=_request_id(ctx),
            instance=decision.instance,
            record_ids=[record_id],
            result_count=1,
        )
        return {
            "success": True,
            "tool": tool,
            "action": approval["action"],
            "record_id": record_id,
            "record_ids": [record_id],
            "model": result.get("model"),
            "state": result.get("state"),
            "idempotent_replay": bool(result.get("replayed")),
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
    except PermissionError:
        code = "access_denied"
        outcome = "denied"
    except Exception:  # noqa: BLE001 - never expose Odoo/network/internal details
        code = "write_failed"
        outcome = "error"
    record_gateway_event(
        "ngv_write_execute",
        outcome=outcome,
        client_id=decision.client_id if decision else current_request_client_id(),
        tool=tool,
        request_id=_request_id(ctx),
        instance=decision.instance if decision else None,
        error_code=code,
    )
    return {
        "success": False,
        "tool": tool,
        "error": (
            "Access denied by gateway policy."
            if code == "access_denied"
            else WRITE_FAILED_MESSAGE
        ),
        "error_code": code,
        "request_id": _request_id(ctx),
    }
