# Báo cáo bàn giao Grok sang Codex — Odoo MCP Ngọc Việt CNC

> Phần P0 mới nhất về write gateway cô lập, OAuth PKCE, CRM write E2E, HTTP
> guard, restore drill và scan image được ghi tại
> `docs/BAO_CAO_CHAT_LUONG_CRM_WRITE_STAGING_2026-10-21.md`.

Ngày kiểm tra: **20-10-2026**  
Repository: `https://github.com/nguyenvinh1234/mcp-odoo`  
PR: `https://github.com/nguyenvinh1234/mcp-odoo/pull/1`  
Branch: `codex/odoo16-readonly-gateway`  
Mốc tiếp nhận đã xác minh: `374b6d6ae87790253b3c537ef9359a3ca0377f35`
Commit sửa Inspector đã kiểm thử CI: `cc3a64990ec8f5beeaf26eff0bcd9eca80d3dce4`

## 1. Phạm vi và ranh giới

- Không ghi, sửa, xóa dữ liệu hoặc restart Odoo production.
- Không merge PR, không public endpoint thiếu xác thực.
- Không thay đổi quyền người dùng, OAuth hoặc secret trong lượt bàn giao này.
- Dữ liệu staging trong bằng chứng dưới đây đã che PII và secret.
- `PASS` chỉ dùng khi có kiểm thử thực tế; thiết kế chưa chạy được ghi `NOT TESTED`.

## 2. Kiểm kê local Grok

Thư mục tiếp nhận: `D:\CHAT GPT WORK\CODEX\mcp-odoo-grok`.

| Mục | Kết quả | Bằng chứng |
|---|---|---|
| Git branch/remote | PASS | Branch đúng; `origin` trỏ repo công ty |
| HEAD local so với GitHub | PASS | Cùng commit `374b6d6` trước khi Codex chỉnh sửa |
| Thay đổi Grok chưa commit | PRESERVED | `CHANGELOG.md` và `scripts/odoo_compose_smoke.py`; không reset/ghi đè |
| Nội dung thay đổi Grok | REVIEWED | Sửa kỳ vọng profile đầy đủ từ 41 lên 47 tool và kiểm tra đủ 6 tool NGV |

Codex giữ thay đổi Grok, bổ sung phân biệt profile đầy đủ với profile chỉ đọc,
thêm regression test và tài liệu này.

## 3. Kết quả mã nguồn và bảo mật

| Hạng mục | Trạng thái | Bằng chứng/kết luận |
|---|---|---|
| Sáu tool NGV | PASS | `ngv_whoami`, leads, customers, quotations, products, daily summary đều đăng ký read-only |
| Bề mặt `ngocviet-readonly` | PASS | MCP Inspector thực tế trả đúng 6 tool; không có `execute_method` |
| Bề mặt profile đầy đủ | PASS | MCP Inspector thực tế trả 47 tool; đây không phải profile dùng cho gateway NGV |
| Phân quyền từng client | PASS (unit/E2E staging) | Tool allowlist + policy scopes + token scopes; hai client khác quyền đã được kiểm tra |
| Thu hồi quyền/token | PASS (unit/E2E staging) | Policy đọc lại mỗi request; token thu hồi bị HTTP 401 |
| Ngăn ghi/xóa | PASS (unit/E2E staging) | Profile từ chối write escape hatch; user Odoo staging không có create/write/unlink |
| Giới hạn dữ liệu | PASS | Field/domain cố định, `limit <= 100`; record rules do Odoo thực thi, không dùng `sudo` |
| Che lỗi nội bộ | PASS | Client chỉ nhận `query_failed` + request ID; exception gốc không trả ra MCP |
| Audit | PASS | Ghi client, tool, instance, record IDs, kết quả, thời lượng; không ghi token/password |
| Odoo 16 adapter | PASS | XML-RPC và xác minh major version 16 |
| Odoo 20 adapter | NOT TESTED | Có khung JSON-2 và chặn sai transport/version; chưa có Odoo 20 thực tế để E2E |

Profile đầy đủ vẫn có tool tổng quát/ghi phục vụ upstream. An toàn của dự án
Ngọc Việt phụ thuộc việc bắt buộc chạy profile `ngocviet-readonly`; regression
test mới sẽ fail nếu profile này lộ thêm tool.

## 4. Kiểm thử local

| Kiểm thử | Kết quả |
|---|---|
| `uv run pytest -q` | PASS — `947 passed, 1 warning` |
| `uv run python -m ruff check .` | PASS |
| `uv run python -m mypy src` | PASS — 37 source files |
| `git diff --check` | PASS |
| Inspector profile đầy đủ | PASS — 47 tool, có 6 tool NGV |
| Inspector `ngocviet-readonly` | PASS — đúng 6 tool, không `execute_method` |

Warning pytest còn lại thuộc test CLI dùng `runpy`: module đã có trong
`sys.modules`; không phải lỗi test và không bị bỏ qua.

MCP Inspector cần truyền biến môi trường bằng các cờ `-e`. Nếu chỉ đặt env ở
tiến trình cha, stdio child chạy profile mặc định và trả 47 tool. JSON quiet của
Inspector dùng `result.tools`; đọc nhầm `tools` cấp cao nhất sẽ cho số 0 giả.

## 5. GitHub Actions tại mốc tiếp nhận

| Job | Trạng thái tại `374b6d6` | Liên quan nghiệm thu |
|---|---|---|
| test Python 3.10/3.11/3.12 | PASS | Có |
| Trivy | PASS | Có |
| build | PASS | Có |
| mcp-inspector | PASS — 2m41s | Có |
| publish/Smithery/MCPB/registry | SKIPPED | Không phải lỗi nghiệm thu trên PR |

Tại mốc tiếp nhận, job `mcp-inspector` FAIL vì smoke script cũ đòi health check
có 41 tool, trong khi server trả đúng 47 tool. Thay đổi Grok sửa 41 thành 47;
Codex bổ sung kiểm tra profile chỉ đọc phải đúng sáu tool. GitHub Actions tại
commit `cc3a649` đã xác nhận lại toàn bộ job nghiệm thu PASS; các job publish
được SKIPPED đúng thiết kế của pull request.

## 6. Bằng chứng staging Odoo 16 đã chạy

Bằng chứng E2E được thu trên Fedora staging, Tailscale `100.75.225.103`, tại
commit `374b6d6`; không dùng hoặc thay đổi dữ liệu production.

| Ca kiểm thử | Kết quả đã ghi nhận |
|---|---|
| MCP không bearer token | PASS — HTTP 401 |
| Bearer token bị thu hồi | PASS — HTTP 401 |
| Hai client khác quyền | PASS — `staging-full-ai`, `staging-crm-ai` |
| CRM-only gọi báo giá | PASS — denied, không chạm Odoo query |
| Ghi dữ liệu bằng hai user | PASS — Odoo từ chối |
| Đọc CRM thật staging | PASS — record IDs `[3, 2, 1]` |
| Đọc báo giá thật staging | PASS — record IDs `[2, 1]` |
| Audit | PASS — 6 success, 1 denied |
| Thời điểm truy vấn mẫu | `2026-10-20T02:20:19.621267+00:00` |

Endpoint staging đã kiểm tra:

- MCP: `https://mcp-staging.ngocvietai.com/mcp`
- OIDC metadata: `https://auth-mcp-staging.ngocvietai.com/realms/mcp-staging/.well-known/openid-configuration`

MCP không xác thực trả 401; gateway chỉ bind host `127.0.0.1:8765`; Odoo,
PostgreSQL và Keycloak không public host port. Không coi các URL này là
production và không mở rộng quyền truy cập trong lượt bàn giao.

Tài nguyên staging đã ghi nhận:

| Tài nguyên | Số liệu |
|---|---|
| RAM host | 31 GiB tổng; 19 GiB dùng; 11 GiB available |
| zram swap | 8/8 GiB đang dùng |
| Disk | 476 GiB tổng; 407 GiB dùng; 69 GiB trống (86%) |
| Gateway | 54.67 MiB |
| Keycloak | 567.9 MiB |
| Keycloak DB | 64.47 MiB |
| Odoo 16 staging | 137.4 MiB |
| PostgreSQL staging | 91.54 MiB |

Không prune, restart hoặc xóa dữ liệu. Swap đầy và disk 86% là rủi ro vận hành;
khắc phục ít ảnh hưởng nhất là đo áp lực bộ nhớ theo tiến trình/container, đặt
memory limit hợp lý cho staging và giải phóng có chọn lọc sau khi có phê duyệt,
không prune mù hoặc restart production.

Trivy trên image server chính xác đã ghi nhận **0 Critical, 0 High**. Gateway
chạy UID 10001, root filesystem read-only và drop toàn bộ capability.

## 7. Kết nối client

| Client | Trạng thái | Giới hạn |
|---|---|---|
| MCP HTTP/OAuth test client | PASS | E2E staging như mục 6 |
| ChatGPT Cloud | BLOCKED | Tài khoản Plus hiện không có giao diện thêm custom MCP; chưa có E2E ChatGPT |
| Claude remote | NOT TESTED | Chưa được cấp client/session thích hợp |
| Claude/Codex local stdio | PASS (Inspector) | Chỉ dùng máy tin cậy và client ID policy riêng |

Nút Connect hoặc OIDC metadata 200 không được coi là E2E. Chỉ đổi ChatGPT/Claude
sang PASS khi chính client gọi `ngv_whoami`, gọi tool dữ liệu và audit ghi đúng
subject.

## 8. Còn thiếu và bước đưa vào sử dụng

1. Chờ GitHub Actions chạy trên commit bàn giao; `mcp-inspector` và toàn bộ job
   nghiệm thu bắt buộc phải PASS.
2. Reviewer kiểm tra PR #1; không merge tự động.
3. Cấp workspace ChatGPT/Claude hỗ trợ custom remote MCP và thực hiện E2E bằng
   client thật; không thay OAuth hiện tại nếu chưa phê duyệt.
4. Trước mọi truy vấn production, tạo sẵn tài khoản Odoo production chuyên dụng
   chỉ đọc và cho người quản trị xác nhận ACL/record rules trong UI. Bước này
   hiện `NOT TESTED` và chưa thực hiện.
5. Chạy thử production chỉ đọc theo kế hoạch được duyệt: `whoami` trước, một
   truy vấn giới hạn nhỏ, kiểm audit/record IDs/timestamp, rồi dừng nếu có sai
   quyền. Tuyệt đối không ghi/xóa/restart.
6. Dựng Odoo 20 riêng và chạy contract/E2E JSON-2 trước khi tuyên bố tương thích.
7. Xử lý sức chứa Fedora theo quy trình backup → thay đổi hẹp → xác minh →
   rollback; giữ nguyên volume cho đến khi restore được kiểm chứng.

## 9. Kết luận nghiệm thu

- **Mã nguồn + unit/security/Inspector local:** PASS.
- **Odoo 16 staging + OAuth + hai quyền + audit:** PASS tại commit `374b6d6`.
- **CI commit sửa Inspector `cc3a649`:** PASS toàn bộ job nghiệm thu.
- **ChatGPT Cloud:** BLOCKED bởi khả năng tài khoản/workspace.
- **Odoo 16 production read-only:** NOT TESTED, production không bị thay đổi.
- **Odoo 20 thực tế:** NOT TESTED.
- **Merge/deploy production:** KHÔNG THỰC HIỆN.
