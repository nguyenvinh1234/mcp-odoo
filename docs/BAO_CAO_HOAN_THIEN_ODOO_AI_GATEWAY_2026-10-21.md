# Báo cáo hoàn thiện Odoo AI Gateway — 21-10-2026

> Cập nhật P0 mới nhất và bằng chứng CRM write staging thực tế nằm tại
> `docs/BAO_CAO_CHAT_LUONG_CRM_WRITE_STAGING_2026-10-21.md`. Báo cáo đó thay
> thế các commit, record ID, số test và số liệu tài nguyên cũ trong tài liệu này.

Repository: `https://github.com/nguyenvinh1234/mcp-odoo`  
PR: `https://github.com/nguyenvinh1234/mcp-odoo/pull/1`  
Branch: `codex/odoo16-readonly-gateway`  
Commit E2E: `32486b4` (CI cuối cùng phải được kiểm tra lại trước merge)

## 1. Ranh giới đã giữ

- Chỉ thay đổi Odoo 16 **staging** `ngv_mcp_staging`; không truy cập hay ghi
  production, không restart production, không merge PR.
- Không dùng tài khoản Odoo admin cho gateway. Ba credential Odoo staging tách
  biệt: full-readonly, CRM-readonly và controlled-writer.
- Odoo/PostgreSQL không được publish bởi Compose. Hai gateway chỉ bind
  `127.0.0.1:8765` và `127.0.0.1:8766`.
- Backup trước nâng cấp: Odoo DB, Keycloak DB và cấu hình/secret tại
  `/home/ngocviet/staging/backups/mcp-odoo/20261020-pre-write/`.
- Không prune Docker, không xóa volume và không ghi secret/token vào Git/log.

## 2. Kết quả nghiệm thu

| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| Sáu tool `ngocviet-readonly` | PASS | E2E thật; đúng 6 tool, lead IDs `[4,3,2,1]`, quotation IDs `[2,1]`, truy vấn `2026-10-21T01:30:12.535475+00:00` |
| Hai user khác ACL | PASS | Odoo user IDs 6 và 7; CRM user chỉ thấy 1 lead và bị từ chối sales/write |
| Không token / token thu hồi | PASS | HTTP 401 / HTTP 401 |
| Token thiếu scope write | PASS | HTTP 403 trước khi gọi Odoo |
| OAuth issuer/audience/introspection | PASS | Issuer Keycloak thật; 3 subject; audience đúng; introspection active; revocation làm token inactive |
| Tạo khách hàng staging | PASS | `res.partner` ID 23 qua prepare → approval → execute |
| Cập nhật CRM staging | PASS | `crm.lead` ID 4; có optimistic lock bằng `write_date` |
| Tạo báo giá nháp staging | PASS | `sale.order` ID 6, state `draft`; giá/UoM lấy phía server |
| Idempotency/retry | PASS | Gọi lại cùng key trả cùng record, `idempotent_replay=true` |
| Hai request đồng thời cùng key | PASS | Chỉ 1 record; request thứ hai là replay |
| Rollback | PASS | Probe cố ý lỗi không để lại partner hoặc receipt |
| Chặn direct write/delete | PASS | Writer gọi thẳng ORM bị từ chối; không có unlink ACL/tool |
| Khác client/khác company | PASS | Full-readonly không dùng tool write; fixture company khác bị ẩn |
| Audit/PII | PASS | 7 success, 1 denied trong lần chạy; token, tên và email test không xuất hiện trong audit |
| HTTPS readonly từ Internet | PASS | `https://mcp-staging.ngocvietai.com/mcp`: TLS hợp lệ, không token trả 401 |
| OIDC metadata từ Internet | PASS | `https://auth-mcp-staging.ngocvietai.com/realms/ngv-mcp-staging/.well-known/openid-configuration`: HTTP 200, PKCE S256, DCR, revocation/introspection |
| HTTPS write từ Internet | BLOCKED/PENDING APPROVAL | Gateway healthy nội bộ; route Cloudflare `mcp-write-staging.ngocvietai.com` đã điền nhưng chưa bấm Add route |
| ChatGPT Business/Enterprise/Edu | NOT TESTED | Chưa có workspace/client thật để gọi `ngv_whoami` và đọc record |
| ChatGPT Plus | BLOCKED | Full custom MCP không thuộc Plus theo tài liệu OpenAI hiện hành; không suy ra PASS từ endpoint |
| ChatGPT Voice | NOT TESTED | Voice không thay đổi quyền app; chưa có custom app khả dụng trên tài khoản để E2E |
| Claude | BLOCKED | Hạn mức/client không sẵn sàng; không tuyên bố kết nối |
| Odoo 16 production readonly | NOT TESTED | Không được chạm production trong lượt này |
| Odoo 20 | NOT TESTED | Chỉ có khung JSON-2/contract; chưa có Odoo 20 thật |

## 3. Kiểm thử mã nguồn và scan

| Kiểm thử | Kết quả |
|---|---|
| pytest | PASS — `956 passed, 1 warning` |
| Ruff | PASS |
| mypy | PASS — 38 source files |
| MCP Inspector | PASS trên CI |
| GitHub build + Python 3.10/3.11/3.12 | PASS tại run trước; kiểm tra lại commit cuối trước merge |
| Trivy image | PASS — job chặn CRITICAL/HIGH |
| `pip-audit` theo lockfile | PASS sau khi nâng `certifi 2026.10.18 → 2026.10.19`; không còn vulnerability đã biết |

Warning pytest còn lại thuộc test CLI `runpy` đã tồn tại; không có test bị skip
để biến FAIL thành PASS.

## 4. Thiết kế write profile

Profile `ngocviet-staging-write` có đúng 10 tool: sáu tool đọc và bốn tool
`ngv_prepare_customer_create`, `ngv_prepare_lead_update`,
`ngv_prepare_draft_quotation`, `ngv_execute_approved_change`.

- Startup fail-closed nếu thiếu ACK staging, cờ enable write, audit log, OAuth,
  scope `odoo.write.staging`, Odoo major 16 hoặc instance không mang tên staging.
- Approval token ngẫu nhiên, ràng buộc client/instance, TTL 10 phút, dùng một lần.
- Payload/field/action được allowlist; không nhận model/method tùy ý.
- Customer có kiểm tra trùng; lead dùng `write_date`; quote luôn draft, tối đa
  20 dòng, server tự lấy tên/giá/UoM sản phẩm.
- Receipt idempotency có unique key và khóa writer transaction để chống race.
- Audit ghi action/outcome/record ID/request ID, không ghi token/password/PII.

## 5. Tài nguyên và rủi ro vận hành

Sau E2E:

- RAM: 31 GiB tổng, 18 GiB dùng, 12 GiB available.
- zram swap: gần 8/8 GiB; đây là cảnh báo lịch sử áp lực bộ nhớ, không đồng
  nghĩa RAM hiện tại đã cạn.
- Disk: 476 GiB tổng, 437 GiB dùng, 40 GiB trống (92%).
- Gateway readonly/write: khoảng 52/54 MiB; Odoo staging 114 MiB; Keycloak
  khoảng 577 MiB.
- Có nhiều image Odoo/PostgreSQL cũ và dangling images. Chưa đủ bằng chứng để
  quy toàn bộ 92% cho dự án này; **không prune tự động**.

Khuyến nghị ít ảnh hưởng nhất: đo `docker system df -v` trong cửa sổ bảo trì,
lập danh sách image/cache không còn container tham chiếu, phê duyệt từng mục,
sau đó mới xóa. Giữ mọi volume/database backup cho tới khi restore được thử.

## 6. Rollback staging

1. Gỡ/disable riêng route Cloudflare write nếu đã tạo; giữ route readonly/auth.
2. Dừng riêng `gateway-write`, sau đó `gateway`; không dừng dịch vụ production.
3. Checkout worktree staging về commit trước và recreate riêng gateway.
4. Nếu module/database có lỗi, dừng Odoo staging, restore
   `odoo.dump`; nếu OAuth lỗi, restore `keycloak.dump`.
5. Khởi động lại từng lớp: DB → Odoo → Keycloak → gateway; chạy OAuth smoke,
   readonly smoke, write smoke. Không xóa backup/volume trước khi xác minh.

## 7. Điều kiện trước production

- Reviewer duyệt PR; toàn bộ CI commit cuối PASS; không merge tự động.
- Dung lượng disk được xử lý có phê duyệt và có ngưỡng cảnh báo.
- Tạo user production **chỉ đọc** bằng quản trị viên Odoo, kiểm tra ACL/record
  rules trực tiếp; gateway tuyệt đối không dùng writer staging cho production.
- E2E client thật phải gọi `ngv_whoami`, một tool dữ liệu, và tạo audit đúng
  subject. Nút Connect/metadata 200 không đủ.
- Write chỉ được xem xét sau một giai đoạn readonly production riêng; cần phê
  duyệt nghiệp vụ, backup/restore drill và rollback đã diễn tập.
- Odoo 20 phải dựng môi trường riêng và chạy contract/E2E JSON-2; hiện không
  được ghi là tương thích đã xác minh.
