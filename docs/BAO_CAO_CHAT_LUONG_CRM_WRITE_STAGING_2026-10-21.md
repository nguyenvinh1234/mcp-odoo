# Báo cáo chất lượng CRM Write Staging — 21-10-2026

Repository: `https://github.com/nguyenvinh1234/mcp-odoo`  
PR: `https://github.com/nguyenvinh1234/mcp-odoo/pull/1`  
Branch: `codex/odoo16-readonly-gateway`  
Commit triển khai và E2E: `263cad0`

## Kết luận

Odoo 16 CRM staging và MCP write gateway đã được triển khai, sửa lỗi và kiểm
thử thực tế. Ba luồng ghi được phép đều PASS qua prepare → approval → execute:
tạo khách hàng, cập nhật CRM và tạo báo giá nháp. Write gateway không có host
port, không nằm trên mạng reverse proxy và chưa có DNS/Cloudflare route.

Không có thay đổi, restart hoặc truy vấn dữ liệu Odoo production. Không merge
PR và không triển khai production.

## Ma trận nghiệm thu

| Hạng mục | Trạng thái | Bằng chứng thực tế |
|---|---|---|
| Cô lập write gateway | PASS | Chỉ mạng internal `ngv-odoo-mcp-staging_backend`; `WRITE_PORTS={}`; không còn `nginx_network` |
| Chặn Odoo production | PASS | Không phân giải `odoo19-web`/`odoo20-web`; host `172.18.0.1:8069` trả network unreachable |
| Odoo production không bị restart | PASS | ID và `StartedAt` của `odoo-web`, `odoo19-web`, `odoo20-web` giữ nguyên từ `2026-10-13T13:17:14Z` |
| OAuth write client | PASS | Public client, Authorization Code, PKCE S256, không password grant, callback loopback chính xác |
| Subject/audience/introspection | PASS | Subject `staging-writer-ai`; có resource-client + URL audience; introspection active |
| Thu hồi token | PASS | Token sau revoke trả inactive và gọi MCP bị HTTP 401 |
| `ngv_whoami` qua PKCE | PASS | Gọi MCP thật thành công với subject writer |
| Sáu tool chỉ đọc/hai ACL | PASS | 6 tool; full user thấy 4 lead, CRM user thấy 1 lead và bị chặn sales/write |
| Tạo khách hàng staging | PASS | Lần E2E cuối: `res.partner` ID `29` |
| Cập nhật CRM staging | PASS | `crm.lead` ID `4`, có optimistic lock |
| Tạo báo giá nháp staging | PASS | `sale.order` ID `8`, state `draft` |
| Idempotency/concurrent retry | PASS | Một record; request còn lại là replay |
| Cross-client/cross-company | PASS | Bị từ chối/ẩn đúng chính sách |
| Direct write/delete | PASS | Direct write bị chặn; không có tool xóa |
| Rollback lỗi nghiệp vụ | PASS | Probe lỗi không để lại partner/receipt |
| HTTP không token | PASS | 401 |
| Thiếu scope write | PASS | 403 |
| Body limit | PASS | Request xác thực 65.537 byte trả 413 |
| Rate limit subject/tool | PASS | 10 request được xử lý; request thứ 11 trả 429 |
| Concurrency/timeout | PASS (unit) | Middleware có limit read/write và deadline; E2E retry đồng thời PASS |
| Audit bảo vệ | PASS | File `0600`, owner UID/GID `10001`; denial có subject/tool/code, không payload/token/PII |
| Audit rotation/retention | PASS (config + unit) | 10 MiB/file, 14 backup; Docker log 10 MiB × 5 |
| Restore backup cô lập | PASS | Run `20261021T082149Z`: Odoo 408 bảng/60 module; Keycloak 101 bảng/2 realm |
| Public HTTPS write | BLOCKED | Không port, không route, không DNS; chờ phê duyệt riêng |
| ChatGPT/Claude thật | NOT TESTED | Không thể test khi endpoint write chưa được công khai và chưa có client được duyệt |
| Odoo production | NOT TOUCHED | Không đọc/ghi/restart production |

## Bằng chứng kiểm thử mã và bảo mật

- `pytest`: `966 passed, 1 warning`.
- Ruff: PASS.
- mypy: PASS, 39 source files.
- Trivy 0.76.0 pinned digest trên image
  `sha256:c1d5bc612843...`: 0 Critical, 0 High cho Alpine và Python packages.
- `pip-audit` từ `uv.lock`: `No known vulnerabilities found`.
- Image runtime: 104.975.120 byte.

Warning pytest còn lại là warning `runpy` đã biết trong test CLI; không có test
bị bỏ qua để biến FAIL thành PASS.

## Backup và khả năng phục hồi

Backup dùng để thử restore, không bị sửa:

- `odoo.dump`: 3.911.678 byte, SHA-256
  `305640992d840052a41c37b85972870a7339aa2e5c8e893ce163e7a6b9296db5`.
- `keycloak.dump`: 253.729 byte, SHA-256
  `55a1e9a779036f0856d0c8ae21eaf1399667c593ed3d20e835d8b0d3fb2a0c8f`.

Restore dùng project riêng `ngv-odoo-mcp-restore-verify`, mạng internal, không
port. Mỗi lần chạy tạo database mới theo run ID; không drop database cũ. Sau
test chỉ stop hai DB restore, giữ nguyên container và volume. Hai volume từ lần
thử lỗi ban đầu cũng được giữ nguyên theo lệnh không xóa.

## Tài nguyên sau kiểm thử

| Tài nguyên | Số liệu |
|---|---|
| RAM host | 31 GiB tổng; 18 GiB dùng; 12 GiB available |
| Swap | 8,0/8,0 GiB đang dùng |
| Disk `/` | 476 GiB tổng; 445 GiB dùng; 31 GiB trống; 94% |
| Write gateway | 53,87 MiB / 512 MiB; 3 PID |
| Readonly gateway | 56,45 MiB / 512 MiB; 3 PID |
| Odoo 16 staging | 108,6 MiB / 1,5 GiB |
| Keycloak staging | 618 MiB / 1 GiB |
| PostgreSQL staging | 104,1 MiB / 768 MiB |

Disk 94% và swap gần đầy là rủi ro vận hành. Không prune, xóa image, volume,
backup hay restart dịch vụ. Cần một phê duyệt riêng để đo dung lượng chi tiết và
xóa chọn lọc các artifact restore thất bại/cache không còn cần thiết.

## Endpoint và điều kiện công khai

Endpoint hiện có chỉ ở mạng Docker staging:

```text
http://gateway-write:8000/mcp
```

Đây không phải URL dành cho client bên ngoài. `mcp-write-staging.ngocvietai.com`
chưa có route/DNS và không được tuyên bố hoạt động.

Trước khi công khai cần phê duyệt riêng, sau đó mới:

1. tạo một đường reverse proxy/Cloudflare chỉ tới write gateway staging;
2. giữ OAuth Authorization Code + PKCE S256 và audience hiện tại;
3. không public Odoo, PostgreSQL, Keycloak internal hoặc Docker socket;
4. E2E từ ngoài LAN: 401/403/413/429, PKCE, revocation, `ngv_whoami`, đọc record;
5. chỉ khi các bước trên PASS mới thử ChatGPT/Claude client thật;
6. rollback bằng cách gỡ riêng route write; không restart production.

## Trạng thái GitHub

PR #1 chưa merge. Tại thời điểm viết báo cáo, build, Python 3.10/3.11/3.12 và
Trivy đã PASS; MCP Inspector còn đang chạy. Phải kiểm tra lại toàn bộ CI tại
commit tài liệu cuối trước khi reviewer duyệt.
