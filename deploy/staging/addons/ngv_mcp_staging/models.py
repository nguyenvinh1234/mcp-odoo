import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


HEX_64 = re.compile(r"^[0-9a-f]{64}$")
CONTROLLED_CONTEXT = "ngv_mcp_controlled_write"


class ControlledWriteMixin:
    def _check_mcp_controlled_write(self):
        if self.env.user.has_group(
            "ngv_mcp_staging.group_mcp_staging_writer"
        ) and not self.env.context.get(CONTROLLED_CONTEXT):
            raise AccessError(_("Use the approval-gated MCP staging workflow."))


class ResPartner(models.Model, ControlledWriteMixin):
    _inherit = "res.partner"

    @api.model_create_multi
    def create(self, vals_list):
        self._check_mcp_controlled_write()
        return super().create(vals_list)

    def write(self, vals):
        self._check_mcp_controlled_write()
        return super().write(vals)


class CrmLead(models.Model, ControlledWriteMixin):
    _inherit = "crm.lead"

    def write(self, vals):
        self._check_mcp_controlled_write()
        return super().write(vals)


class SaleOrder(models.Model, ControlledWriteMixin):
    _inherit = "sale.order"

    @api.model_create_multi
    def create(self, vals_list):
        self._check_mcp_controlled_write()
        return super().create(vals_list)


class SaleOrderLine(models.Model, ControlledWriteMixin):
    _inherit = "sale.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        self._check_mcp_controlled_write()
        return super().create(vals_list)


class NgvMcpRequest(models.Model):
    _name = "ngv.mcp.request"
    _description = "Ngoc Viet MCP staging idempotency receipt"
    _order = "id desc"

    request_key = fields.Char(required=True, index=True, readonly=True)
    client_digest = fields.Char(required=True, readonly=True)
    payload_hash = fields.Char(required=True, readonly=True)
    action = fields.Selection(
        [
            ("customer.create", "Create customer"),
            ("lead.update", "Update CRM lead"),
            ("quotation.create_draft", "Create draft quotation"),
        ],
        required=True,
        readonly=True,
    )
    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    user_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )

    _sql_constraints = [
        (
            "request_key_unique",
            "unique(request_key)",
            "The MCP staging idempotency key already exists.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.user.has_group(
            "ngv_mcp_staging.group_mcp_staging_writer"
        ) and not self.env.context.get(CONTROLLED_CONTEXT):
            raise AccessError(_("Use the controlled request executor."))
        return super().create(vals_list)

    @api.model
    def execute_request(
        self, action, request_key, payload_hash, client_digest, payload
    ):
        if not self.env.user.has_group("ngv_mcp_staging.group_mcp_staging_writer"):
            raise AccessError(_("Controlled staging writer access is required."))
        if action not in dict(self._fields["action"].selection):
            raise ValidationError(_("Unsupported controlled write action."))
        for label, value in (
            ("request_key", request_key),
            ("payload_hash", payload_hash),
            ("client_digest", client_digest),
        ):
            if not isinstance(value, str) or not HEX_64.fullmatch(value):
                raise ValidationError(_("Invalid %s.") % label)
        if not isinstance(payload, dict):
            raise ValidationError(_("Write payload must be an object."))

        # Serialize the same idempotency key across concurrent workers. The
        # lock ends with the transaction and never escapes this staging addon.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [request_key]
        )
        existing = self.search([("request_key", "=", request_key)], limit=1)
        if existing:
            if (
                existing.action != action
                or existing.payload_hash != payload_hash
                or existing.client_digest != client_digest
            ):
                raise ValidationError(_("Idempotency key conflicts with another payload."))
            return {
                "model": existing.res_model,
                "record_id": existing.res_id,
                "replayed": True,
                "state": self._safe_state(existing.res_model, existing.res_id),
            }

        if action == "customer.create":
            record = self._create_customer(payload)
        elif action == "lead.update":
            record = self._update_lead(payload)
        else:
            record = self._create_draft_quotation(payload)

        if self.env.context.get("ngv_mcp_test_rollback"):
            raise UserError(_("Intentional staging rollback probe."))

        self.with_context(**{CONTROLLED_CONTEXT: True}).create(
            {
                "request_key": request_key,
                "client_digest": client_digest,
                "payload_hash": payload_hash,
                "action": action,
                "res_model": record._name,
                "res_id": record.id,
                "user_id": self.env.user.id,
            }
        )
        return {
            "model": record._name,
            "record_id": record.id,
            "replayed": False,
            "state": record.state if "state" in record._fields else None,
        }

    @api.model
    def _safe_state(self, model_name, record_id):
        if model_name not in {"res.partner", "crm.lead", "sale.order"}:
            return None
        record = self.env[model_name].browse(record_id).exists()
        return record.state if record and "state" in record._fields else None

    @api.model
    def _create_customer(self, payload):
        allowed = {"name", "company_type", "email", "phone", "vat"}
        if set(payload) - allowed:
            raise ValidationError(_("Customer payload contains unsupported fields."))
        name = " ".join(str(payload.get("name") or "").split()).strip()
        company_type = str(payload.get("company_type") or "").strip()
        if not name or len(name) > 120 or company_type not in {"company", "person"}:
            raise ValidationError(_("Invalid customer name or company type."))
        email = str(payload.get("email") or "").strip() or False
        vat = str(payload.get("vat") or "").strip() or False
        duplicate_domain = [("name", "=ilike", name)]
        if email:
            duplicate_domain = ["|", ("name", "=ilike", name), ("email", "=ilike", email)]
        if vat:
            duplicate_domain = ["|", ("vat", "=", vat)] + duplicate_domain
        if self.env["res.partner"].search(duplicate_domain, limit=1):
            raise ValidationError(_("Possible duplicate customer."))
        values = {
            "name": name,
            "company_type": company_type,
            "customer_rank": 1,
            "email": email,
            "phone": str(payload.get("phone") or "").strip() or False,
            "vat": vat,
            "company_id": self.env.company.id,
        }
        return self.env["res.partner"].with_context(
            **{CONTROLLED_CONTEXT: True}
        ).create(values)

    @api.model
    def _update_lead(self, payload):
        if set(payload) != {"lead_id", "expected_write_date", "values"}:
            raise ValidationError(_("Lead update payload shape is invalid."))
        lead = self.env["crm.lead"].browse(int(payload["lead_id"])).exists()
        if not lead:
            raise AccessError(_("Lead is missing or outside record rules."))
        expected = str(payload.get("expected_write_date") or "")
        if fields.Datetime.to_string(lead.write_date) != expected:
            raise ValidationError(_("Lead changed after approval preview."))
        values = payload.get("values")
        allowed = {"name", "probability", "expected_revenue", "stage_id", "user_id"}
        if not isinstance(values, dict) or not values or set(values) - allowed:
            raise ValidationError(_("Lead update contains unsupported fields."))
        if "probability" in values and not 0 <= float(values["probability"]) <= 100:
            raise ValidationError(_("Probability must be between 0 and 100."))
        if "expected_revenue" in values and not 0 <= float(values["expected_revenue"]) <= 1_000_000_000_000:
            raise ValidationError(_("Expected revenue is outside the supported range."))
        if "stage_id" in values:
            stage = self.env["crm.stage"].browse(int(values["stage_id"])).exists()
            if not stage:
                raise ValidationError(_("CRM stage does not exist."))
        if "user_id" in values:
            user = self.env["res.users"].browse(int(values["user_id"])).exists()
            if not user or self.env.company not in user.company_ids:
                raise ValidationError(_("CRM assignee is outside the current company."))
        lead.with_context(**{CONTROLLED_CONTEXT: True}).write(values)
        return lead

    @api.model
    def _create_draft_quotation(self, payload):
        if set(payload) != {"partner_id", "client_order_ref", "lines"}:
            raise ValidationError(_("Quotation payload shape is invalid."))
        partner = self.env["res.partner"].browse(int(payload["partner_id"])).exists()
        if not partner:
            raise AccessError(_("Customer is missing or outside record rules."))
        lines = payload.get("lines")
        if not isinstance(lines, list) or not 1 <= len(lines) <= 20:
            raise ValidationError(_("Quotation requires between 1 and 20 lines."))
        order_lines = []
        seen = set()
        for line in lines:
            if not isinstance(line, dict):
                raise ValidationError(_("Quotation line must be an object."))
            product_id = int(line.get("product_id") or 0)
            quantity = float(line.get("quantity") or 0)
            if product_id in seen or quantity <= 0 or quantity > 10000:
                raise ValidationError(_("Invalid or duplicate quotation product."))
            seen.add(product_id)
            product = self.env["product.product"].browse(product_id).exists()
            if not product or not product.active or not product.sale_ok:
                raise AccessError(_("Product is unavailable or outside record rules."))
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": product.id,
                        "name": product.get_product_multiline_description_sale(),
                        "product_uom": product.uom_id.id,
                        "product_uom_qty": quantity,
                        "price_unit": product.lst_price,
                    },
                )
            )
        order = self.env["sale.order"].with_context(
            **{CONTROLLED_CONTEXT: True}
        ).create(
            {
                "partner_id": partner.id,
                "user_id": self.env.user.id,
                "client_order_ref": str(payload.get("client_order_ref") or "").strip() or False,
                "order_line": order_lines,
            }
        )
        if order.state != "draft":
            raise ValidationError(_("Controlled writer may create draft quotations only."))
        return order
