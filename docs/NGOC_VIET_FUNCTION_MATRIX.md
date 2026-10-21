# Ma trận nghiệp vụ và kiểm thử 16 → 20

| Phân hệ | Giai đoạn hiện tại | Model chính | Phụ thuộc | Kiểm thử bắt buộc trước khi mở |
|---|---|---|---|---|
| CRM | Read-only đã viết | `crm.lead` | `crm`, sales team, company rules | owner/team/company, ID trái quyền, lead archive, timezone |
| Khách hàng | Read-only đã viết | `res.partner` | Contacts, company hierarchy | child/company, customer rank, field PII, record rules |
| Bán hàng/báo giá | Read-only đã viết | `sale.order` | `sale_management`, pricelist, currency | draft/sent definition, totals, multi-company |
| Sản phẩm | Read-only đã viết | `product.template` | Product, UoM, pricelist | variant/template, cost field denial, archived product |
| Mua hàng | Chưa mở | `purchase.order` | Purchase, vendor ACL | RFQ/order states, vendor visibility, amount fields |
| Kho | Chưa mở | `stock.quant`, `stock.picking` | Inventory, warehouse/location rules | lot/serial, location/company, reserved qty, no validate |
| Kế toán | Chưa mở | `account.move`, `account.move.line` | Accounting, fiscal localization | field-level confidentiality, company, no post/reset |
| Nhân sự | Chưa mở | `hr.employee` | Employees, private/public models | private fields, manager hierarchy, cross-company |
| Sản xuất | Chưa mở | `mrp.production`, `mrp.bom` | MRP, stock, product | BOM visibility, work center, no confirm/produce |
| Báo cáo | Chưa mở | `read_group` cố định | Từng model ở trên | aggregate không vượt record rules, timeout, cardinality |

Mỗi phân hệ mới phải có tool chuyên biệt, field/domain cố định, scope riêng,
Odoo ACL/record-rule test, giới hạn record/timeout và audit. Không mở lại generic
`search_records`, `execute_method`, resource tùy model hoặc SQL/Python.

## Khác biệt adapter

| Hạng mục | Odoo 16 | Odoo 20 |
|---|---|---|
| Transport | XML-RPC | JSON-2 |
| Credential | password/API key của user riêng | API key mới của user riêng |
| Adapter | `Odoo16ReadOnlyAdapter` | `Odoo20ReadOnlyAdapter` |
| Gate version | major phải bằng 16 | major phải bằng 20 |
| Trạng thái runtime | Cần test staging thật | Chỉ có cấu trúc, chưa test |

Trước cutover phải đối chiếu lại model/field/state, user/company/group, record
rules, dữ liệu nhập và kết quả cùng bộ test. MCP không thực hiện migration ERP.
