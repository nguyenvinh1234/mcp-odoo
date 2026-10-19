# Báo cáo kiểm thử 19/10/2026

## Kết quả đã chạy

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| Unit/regression | PASS | `942 passed`, 1 cảnh báo `runpy` không làm fail |
| Ruff | PASS | `All checks passed` với Ruff 0.15.22 |
| mypy | PASS | 37 source files, không có issue |
| Compose parse | PASS | `docker compose ... config --quiet`, exit 0 |
| Dependency audit | PASS | `pip-audit`: `No known vulnerabilities found`; package local `odoo-mcp` không có trên PyPI nên bị skip theo tên package |
| Secret tĩnh cơ bản | PASS | Không commit file secret thật; chỉ có placeholder `.example`; secret runtime qua mount |
| Build image | BLOCKED | Docker Desktop Linux daemon không chạy: named pipe `dockerDesktopLinuxEngine` không tồn tại |
| Image vulnerability scan | NOT TESTED | Chưa build được image nên chưa chạy Trivy/Grype |
| Odoo 16 staging runtime | NOT TESTED | Chưa có Docker daemon và chưa tạo user/API key staging chỉ đọc |
| Odoo 20 runtime | NOT TESTED | Adapter boundary đã test; chưa có E2E Odoo 20 |
| ChatGPT/Claude/Codex E2E | NOT TESTED | Chưa có OAuth provider/endpoint staging hoạt động |
| Odoo production | NOT TOUCHED | Không SSH, không cài module, không restart, không đọc/ghi production |

Lockfile sau vá gồm MCP 2.3.0, AnyIO 4.15.1, cryptography 50.0.2,
httpx2/httpcore2 2.14.0, PyJWT 2.15.1, python-multipart 0.0.32,
Starlette 1.9.0 và urllib3 2.8.0.

## Kiểm thử gateway có trong code

- Client/tool/scope đúng được cho phép; thiếu scope bị chặn trước RPC.
- Sửa policy `enabled=false` có hiệu lực ở lần gọi sau.
- Startup từ chối write, unknown method và direct chatter escape hatch.
- Profile chỉ còn đúng 6 tool; generic tool/resource/template/prompt bị loại.
- Adapter 16 từ chối transport không phải XML-RPC; adapter 20 yêu cầu JSON-2.
- Bài tổng hợp trả ID lead/báo giá, timestamp UTC, nhóm người phụ trách và không
  ghi bearer token vào audit.
- Phản hồi lỗi phân quyền/lỗi RPC được chuẩn hóa; kiểm thử xác nhận không trả
  nội dung exception, chuỗi kết nối hoặc mật khẩu nội bộ cho MCP client/audit.
- Symlink swap khi đọc file upload bị từ chối trên Windows và Linux.

## Bài nghiệm thu dữ liệu thật còn phải chạy

Câu “Hôm nay có bao nhiêu khách hàng tiềm năng, bao nhiêu báo giá chưa xử lý và
ai phụ trách?” chỉ được ghi PASS sau khi `ngv_daily_sales_summary` chạy trên
Odoo 16 staging/được phép, đối chiếu cùng user trong UI, lưu record ID và
`queried_at`, rồi kiểm tra audit. Hiện tại: **NOT TESTED**, không có số liệu thật.
