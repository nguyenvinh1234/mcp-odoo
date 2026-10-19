# Báo cáo chạy staging Odoo MCP ngày 19/10/2026

Phạm vi là Compose project `ngv-odoo-mcp-staging` tại
`/home/ngocviet/staging/mcp-odoo-pr1`. Không sử dụng database, volume, network
hoặc credential của Odoo production. Không merge PR.

## Chẩn đoán Fedora trước triển khai

- RAM 31 GiB; available dao động 6,6–8,0 GiB.
- zram swap 8 GiB đã dùng gần 100%, nhưng mẫu `vmstat 1 5` có swap-in/out gần 0,
  CPU idle 81–89%; đây là áp lực bộ nhớ tích lũy, chưa phải thrashing tức thời.
- PID Node trong `openclaw-openclaw-gateway-1` giữ khoảng 4,6 GiB swap và không
  có memory limit. Metabase khoảng 307 MiB swap, Hermes khoảng 283 MiB; phần còn
  lại phân tán.
- Docker daemon vẫn phản hồi `info`, `ps`, `stats`; máy có 64 container, 42 đang
  chạy, 136 volume và 44 image. Chỉ `docker system df` vượt timeout 15 giây,
  phù hợp với phép quét dung lượng chậm trên nhiều volume, không phải daemon treo.
- Root filesystem 476 GiB, dùng 82% trước staging và 83% sau build.

Khuyến nghị ít ảnh hưởng nhất: chưa `swapoff`, prune hoặc restart. Theo dõi
`MemAvailable`, `si/so`, PSI/OOM; đặt memory limit cho OpenClaw trong một cửa sổ
bảo trì riêng sau khi review cấu hình. Staging được giới hạn cứng và chỉ bind
gateway vào `127.0.0.1:8765`.

## Kiến trúc staging đã chạy

| Service | Giới hạn RAM | Kết quả |
|---|---:|---|
| PostgreSQL 15 staging | 768 MiB | healthy |
| Odoo 16 staging | 1,5 GiB | healthy |
| OAuth RFC 7662 test introspection | 128 MiB | healthy, không public port |
| MCP Gateway | 512 MiB | healthy, UID/GID 10001, rootfs read-only |

OAuth stub chỉ phục vụ test nội bộ, dùng opaque token ngẫu nhiên từ Docker
secret và không phải authorization server production. Gateway bắt buộc issuer,
audience, scope, introspection và claim `sub`.

## E2E thực tế

Kết quả smoke tại `2026-10-19T14:30:22.901442+00:00`:

- Không bearer token: HTTP 401.
- MCP surface: đúng 6 tool, không generic tool.
- Subject `staging-full-ai` → Odoo user ID 6: đọc được 2 CRM lead (ID 2, 1)
  và 1 báo giá draft (ID 1).
- Subject `staging-crm-ai` → Odoo user ID 7: đọc CRM được; gọi tool báo giá bị
  `access_denied`; truy cập trực tiếp `sale.order` cũng bị Odoo ACL từ chối.
- Thử `crm.lead.create` bằng cả hai Odoo user đều bị ACL từ chối.
- Audit: 6 event success, 1 event denied; có client/tool/instance/record ID/
  request ID/timestamp; smoke xác nhận không có hai bearer token trong file log.

Tài nguyên sau E2E:

| Service | RAM thực tế |
|---|---:|
| PostgreSQL | 191,5 MiB |
| Odoo 16 | 141,4 MiB |
| OAuth stub | 15,9 MiB |
| MCP Gateway | 62,2 MiB |

Host sau E2E còn khoảng 7,9 GiB RAM available; zram vẫn đầy nhưng không tăng do
stack staging. Odoo production `odoo-web` và `odoo-db` vẫn có uptime 6 ngày.

## FAIL đã gặp và khắc phục

1. Secret mode 0600 không đọc được bởi UID container: giữ thư mục 0700, đổi file
   bind-mounted secret thành 0644; `.env` vẫn 0600.
2. Cú pháp tmpfs YAML bị tách tại dấu phẩy: quote toàn bộ mount option.
3. MCP SDK 2.3 đổi API client và nhận header qua `httpx2.AsyncClient`: cập nhật
   smoke test theo signature runtime.
4. Host-header defense chặn hostname Compose: thêm duy nhất `gateway:8000` vào
   allowlist nội bộ; không mở thêm port.

## Việc chưa đạt production

- Chưa chạy Trivy/Grype image scan.
- Chưa dùng authorization server/OAuth client production hoặc public HTTPS.
- Chưa kết nối ChatGPT Cloud/Claude; gateway vẫn loopback-only.
- Chưa thử dữ liệu khách hàng production và không được coi dữ liệu fixture là
  kết quả kinh doanh thật.
