# Hướng dẫn Odoo MCP Gateway Ngọc Việt CNC

## Trạng thái và ranh giới

Mã nguồn này cung cấp profile `ngocviet-readonly` cho giai đoạn 1. Profile chỉ
đọc CRM, khách hàng, báo giá và sản phẩm; không đăng ký tool ghi/xóa, tool gọi
method tùy ý, MCP resource tùy model hay prompt workflow. Odoo production không
được thay đổi bởi cấu hình staging này.

Chưa được phép coi hệ thống là sẵn sàng production cho đến khi toàn bộ bảng
nghiệm thu phía dưới chạy PASS với tài khoản Odoo thật có ACL/record rule đúng.

## Kiến trúc

```text
ChatGPT / Claude / Codex
        | OAuth 2.1 bearer (HTTP) hoặc tiến trình stdio riêng
        v
MCP Gateway profile ngocviet-readonly
        | subject -> policy -> Odoo instance/credential riêng
        v
Odoo 16 adapter (XML-RPC) / Odoo 20 adapter (JSON-2)
        | ORM dưới user không phải admin
        v
Odoo ACL + record rules
```

Gateway không nhận `user_id`, database hay model do AI tự khai. `sub` của token
OAuth được ánh xạ trong `client_policy.json` tới một instance đã cấu hình sẵn.
Mỗi instance phải dùng credential Odoo riêng để record rules có hiệu lực đúng
người/nhóm. Tắt client hoặc bỏ tool/scope trong policy có hiệu lực ở lần gọi kế
tiếp vì policy được đọc lại từng request.

## Sáu tool giai đoạn 1

| Tool | Scope | Dữ liệu |
|---|---|---|
| `ngv_whoami` | `odoo.read` | Danh tính Odoo hiệu lực, không trả login/key |
| `ngv_list_leads` | `odoo.crm.read` | `crm.lead`, field cố định, phân trang tối đa 100 |
| `ngv_list_customers` | `odoo.customers.read` | `res.partner` có `customer_rank > 0`, không email/điện thoại |
| `ngv_list_quotations` | `odoo.sales.read` | `sale.order` trạng thái `draft`/`sent` |
| `ngv_list_products` | `odoo.products.read` | `product.template` đang bán |
| `ngv_daily_sales_summary` | CRM + Sales | Lead tạo trong ngày và báo giá chưa xử lý, record ID, người phụ trách, thời điểm truy vấn |

“Báo giá chưa xử lý” được định nghĩa rõ là state `draft` hoặc `sent`. “Lead hôm
nay” là lead active có `create_date` nằm trong ngày lịch tại
`Asia/Ho_Chi_Minh`; truy vấn Odoo dùng biên UTC.

## Dựng staging riêng

1. Vào `deploy/staging`, sao chép `.env.example` thành `.env`.
2. Trong `secrets/`, sao chép bốn file `.example` sang tên bỏ đuôi `.example`.
3. Thay toàn bộ placeholder. Không dùng admin hoặc credential production.
4. Tạo user Odoo staging chỉ có quyền đọc các model cần thiết. Đăng nhập UI bằng
   user đó để xác nhận record rules trước khi đưa API key vào secret.
5. Cấu hình Authorization Server hỗ trợ RFC 7662 introspection, audience đúng
   `MCP_RESOURCE_URL`, issuer đúng tuyệt đối và scope nêu trên.
6. Kiểm tra cấu hình rồi mới chạy:

```bash
docker compose --env-file deploy/staging/.env \
  -f deploy/staging/compose.yml config --quiet
docker compose --env-file deploy/staging/.env \
  -f deploy/staging/compose.yml up -d --build
```

Compose chỉ publish gateway lên `127.0.0.1`; Odoo và PostgreSQL không publish
host port. Đưa HTTPS reverse proxy đã xác thực ở cùng máy lên trước port này.
Gateway chạy UID/GID `10001`, filesystem chỉ đọc, drop toàn bộ Linux capabilities,
không mount Docker socket. Audit nằm ở volume `gateway-audit`.

## Quyền Odoo tối thiểu

- Tạo nhóm riêng `MCP Readonly`; chỉ cấp read ACL cho `crm.lead`, `res.partner`,
  `sale.order`, `product.template`, `res.users` (chỉ đủ để đọc chính user).
- Áp record rules theo sales team/company/owner như UI; không dùng `sudo`,
  superuser, admin hay rule “mọi bản ghi”.
- Không cấp create/write/unlink. Không cấp Settings, Technical Features,
  Export, attachment/chatter nếu chưa có test riêng.
- Mỗi AI/nhân viên cần ranh giới dữ liệu khác nhau phải dùng một Odoo credential
  và một instance mapping riêng. Không dùng một service account admin chung.

## Kết nối client

### ChatGPT qua HTTPS

Dùng endpoint `https://<host>/mcp`. Authorization Server phải phát token có
`aud` bằng đúng resource URL, `iss` đúng cấu hình và `sub` trùng khóa trong
client policy. Chỉ đánh dấu PASS khi ChatGPT thực sự gọi `ngv_whoami`, sau đó
gọi một tool dữ liệu và audit ghi cùng client ID. Nút Connect không phải bằng
chứng E2E.

### Claude/Codex local qua stdio

Stdio chỉ dành cho một tiến trình riêng trên máy tin cậy. Đặt:

```text
ODOO_MCP_SECURITY_PROFILE=ngocviet-readonly
ODOO_MCP_NGV_ALLOW_STDIO=1
ODOO_MCP_NGV_STDIO_CLIENT_ID=<client trong policy>
ODOO_MCP_NGV_CLIENT_POLICY_FILE=<đường dẫn policy>
ODOO_MCP_AUDIT_LOG=<đường dẫn audit>
ODOO_CONFIG_FILE=<đường dẫn cấu hình instance>
```

Không bật `ALLOW_STDIO` trên gateway HTTP dùng chung. Claude Desktop có thể dùng
stdio local; Claude remote/ChatGPT phải qua HTTPS OAuth. Khả năng web, iPhone và
Voice phải báo PASS/FAIL/NOT TESTED riêng.

## Nghiệm thu và rollback

Chạy unit test:

```bash
uv sync --extra dev --locked
uv run pytest -q
uv run python -m ruff check .
uv run python -m mypy src
```

Trên staging, dùng hai user có record rules khác nhau và lưu bằng chứng đã che
PII cho: token thiếu/sai/thu hồi; `whoami`; cùng query khác kết quả; đọc ID trái
quyền; song song hai user/company; query lớn/timeout; audit; và câu hỏi mẫu.

Rollback gateway: dừng riêng project Compose, giữ nguyên volumes để điều tra,
đưa reverse proxy về endpoint trước đó. Không xóa volume cho đến khi backup và
khả năng restore đã được xác minh. Chuyển 16 sang 20 phải tạo credential mới,
đổi `instance` + `odoo_major` sau test 20; không mang record ID/key từ 16 sang 20.
