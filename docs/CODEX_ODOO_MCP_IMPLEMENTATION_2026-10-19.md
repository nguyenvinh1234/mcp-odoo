# CODEX TASK — Odoo 16 MCP Gateway (19-10-2026)

## Mục tiêu
Tạo MVP ChatGPT/Claude/Grok → authenticated MCP gateway → Odoo 16, **đọc dữ liệu thật** CRM/báo giá/sản phẩm với quyền tối thiểu. Không đụng dữ liệu Odoo production cho đến khi kiểm thử và duyệt. Thiết kế adapter cho Odoo 20 về sau.

## Nghiên cứu mã nguồn
1. Audit repo hiện tại và tham chiếu https://github.com/ivnvxd/mcp-server-odoo (license, maintenance, tests, transport, ACL, injection, arbitrary Odoo RPC, HTTP auth).
2. Ghi rõ file:line của lỗ hổng; không coi README là chứng cứ đủ và không bật chế độ bỏ qua ACL.
3. Giữ license/attribution và pin versions.

## Implementation
- Gateway MCP HTTPS authenticated, per-client identity, allowlist tools, structured audit logs (lọc PII/secrets), timeout/rate limit; tuyệt đối không expose Odoo hay một MCP không auth ra public Internet.
- Odoo adapter 16 service account least privilege; secrets qua env/secret mounts; không dùng root và không mount docker.sock.
- Tools read-only allowlisted: crm.lead, res.partner, sale.order, product.template/product.product và stock.quant (chỉ khi module có). Không cho AI tự chọn model/method/SQL/Python bất kỳ.
- Field allowlist, domain validation, pagination, limits, tuân thủ Odoo record rules.
- Phân tách lớp adapter để chuyển 16→20 sau này; không migration dữ liệu.
- Có mô hình approval cấp server cho tương lai (request ID, diff, expiration, single-use, audit); hiện tại **deny mọi write/delete, confirm/post và chạy business action**.
- Dockerfile + staging compose override/healthcheck + env.example placeholder, không đụng compose production.

## Test/DoD
- Unit/integration mocked: auth/denial, ACL, field/domain injection, pagination, empty records, timeout, logging, replay approval.
- Staging smoke: 5 CRM leads + 5 quotations, thống kê lead theo người phụ trách, trả Odoo record IDs/timestamp. Chỉ đánh dấu PASS khi thực sự chạy.
- Báo cáo tương thích ChatGPT Cloud bằng kiểm tra năng lực client, không tuyên bố đã kết nối nếu chưa end-to-end.
- Tài liệu tiếng Việt: setup staging, hệ thống quyền, audit, backup/restore, checklist triển khai, ma trận CRM/Sales/Purchase/Inventory/Accounting/HR/Manufacturing, gap cho Odoo20.
- Tạo PR riêng để review; **không tự merge, deploy production, đổi credentials, cài module, restart container hoặc ghi/xóa Odoo**.
- Trả PR URL, lệnh test, output test, findings, blockers, các thứ cần phê duyệt tiếp.

## Bối cảnh
Server Fedora / Docker, Odoo16 active; Odoo20 separate; production credentials và host values phải tự xác minh trong môi trường được cấp quyền, không đoán. Khuyến khích staging isolation. Phải giữ an toàn Odoo16 và dữ liệu CRM thật.

## Chỉ dẫn Codex
Bắt đầu bằng đọc repo, lập kế hoạch ngắn, coding qua branch và PR, chạy test; nếu chưa có server access vẫn thực hiện code+mock test và ghi rõ thiếu kết nối. Không hỏi lại các yêu cầu đã ghi.