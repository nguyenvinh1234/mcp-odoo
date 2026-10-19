# Báo cáo nghiên cứu và bảo mật

Ngày đánh giá: 19/10/2026. Phạm vi: commit công ty `ff242a8` và repo tham khảo
`ivnvxd/mcp-server-odoo` được clone cùng ngày. Đây là audit mã tĩnh + unit test;
không phải chứng nhận an toàn production.

## Lựa chọn nền

Repo công ty dùng MIT, Python >=3.10, dependency trực tiếp `mcp>=2,<3` và
`requests>=2.31.0`, có lockfile và bộ test rộng. Kiến trúc server ngoài Odoo,
MCP stdio/Streamable HTTP/SSE → client Odoo XML-RPC (16–18) hoặc JSON-2
(19+). Mã đã có multi-instance, field policy, rate limit, approval write,
OAuth introspection và audit write, nên phù hợp để tái sử dụng.

Repo `ivnvxd/mcp-server-odoo` dùng MPL-2.0, `mcp>=2.2,<3`, pydantic và dotenv.
Standard mode phụ thuộc module Odoo MCP riêng; YOLO mode truy cập RPC trực tiếp.
README của repo này xác nhận HTTP không có client auth tích hợp và YOLO full có
blast radius lớn. Vì repo công ty đã có OAuth, test và adapter RPC trực tiếp,
không sao chép nền ivnvxd; chỉ dùng làm đối chiếu transport, context và cảnh báo.

## Phát hiện xác nhận trong repo công ty trước thay đổi

| Mức | Phát hiện | Tác động | Xử lý profile Ngọc Việt |
|---|---|---|---|
| Cao | HTTP auth là tùy chọn; bind remote có thể chạy sau cờ cho phép | Credential Odoo bị dùng bởi client mạng không xác thực | HTTP profile bắt buộc cấu hình OAuth introspection, issuer/audience |
| Cao | Generic `search_records` nhận model/domain/fields; `fields=["*"]` bỏ smart filtering | AI có thể đọc mọi model/field mà service account được ACL | Loại tool khỏi registry; chỉ tool nghiệp vụ field cố định |
| Cao | `execute_method` và write workflow tồn tại, có escape hatch bằng env/policy | Có thể chạy business action/ghi nếu cấu hình sai | Startup từ chối write/unknown/chatter env; tool bị loại |
| Cao | MCP resources nhận model và record ID tùy ý | Bypass tool allowlist | Profile xóa toàn bộ resource/template/prompt surface |
| Trung bình | Audit gốc chủ yếu cho write | Thiếu dấu vết truy vấn read | Audit metadata mọi tool Ngọc Việt, không ghi payload/token |
| Trung bình | OAuth `client_id` có thể là app dùng chung | Không phân biệt người dùng ChatGPT | Cho chọn claim `sub`; staging bắt buộc `sub` |
| Trung bình | Một config credential không tự tạo ACL từng AI | Dễ dùng service account chung quá quyền | Policy ánh xạ subject → instance/credential Odoo riêng |
| Thấp | Test import `tests.*` lỗi collection trên Windows | Không có baseline test tin cậy | Thêm package marker `tests/__init__.py` |

Gateway không dùng `sudo`, SQL, `safe_eval`, subprocess hay lệnh shell cho truy
vấn nghiệp vụ. Adapter chỉ gọi `search_read`, `search_count`, `read_group`; model,
method, field và domain shape được mã cố định. Odoo vẫn thực thi ACL và record
rules của credential tương ứng.

## Giới hạn và việc chưa xác minh

- Chưa có credential/đường kết nối staging Odoo Ngọc Việt trong lượt này; chưa
  chạy câu hỏi mẫu trên dữ liệu thật.
- Chưa test hai user/company đồng thời, field ACL tùy biến, attachment/chatter,
  thu hồi quyền Odoo đang kết nối, timeout mạng thật hoặc OAuth provider thật.
- Adapter Odoo 20 mới là cấu trúc và unit boundary; chưa chạy Odoo 20 runtime.
- Docker image/dependency vulnerability scan và image signature cần chạy trong
  CI có Trivy/Grype/pip-audit và lưu artifact; kết quả chưa có thì phải ghi
  `NOT TESTED`, không suy đoán.
- ChatGPT web, Claude remote/Desktop, Codex, iPhone và Voice chưa E2E.

## Điều kiện production

Backup staging/production config; tạo user Odoo chỉ đọc; review ACL/record rules;
chạy toàn bộ test matrix; scan dependency/image; kiểm tra audit/retention/restore;
đặt HTTPS proxy; E2E từng client; review PR; sau đó mới trình thay đổi production
cụ thể để phê duyệt. Không merge, deploy, restart hay cài module production từ
nhánh này.
