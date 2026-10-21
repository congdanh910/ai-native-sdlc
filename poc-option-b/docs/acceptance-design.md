# Preview và Acceptance

_2026-10-21. Người dùng chọn: làm preview theo run (nút Try it) và bước Acceptance (E2E có bằng chứng, cổng QC cấu
hình được, mặc định gated). Preview và regression của `main`: BACKLOG 3.8._

## Vấn đề

Nền tảng mới chứng minh code qua test (unit, API với bản giả). Hết một US, không ai chạy ứng dụng thật; PO/QC không có
chỗ bấm thử; tiêu chí chấp nhận không có bằng chứng qua giao diện.

## P1: preview theo run

`.sdlc/project.yaml`, khối `preview` (bước Onboarding viết; run Foundation dựng file compose, dữ liệu mẫu, tài khoản):

```yaml
preview:
  compose: .sdlc/preview.compose.yml   # docker compose: app, database, bản giả, seed
  service: web                         # service mở ra máy, chỉ trên 127.0.0.1
  port: 8080                           # cổng trong container của service đó
  path: /                              # trang Try it mở
  health_path: /health                 # chờ trả 200
  start_timeout_seconds: 600
  accounts:                            # tài khoản demo, chỉ dữ liệu test
    - {role: HR_ADMIN, username: hr@example.test, password: Demo-123}
  e2e: [npx, playwright, test, --config, e2e/playwright.config.ts]
  e2e_image: mcr.microsoft.com/playwright:v1.49.1-noble
  e2e_paths: [e2e/**]
```

- Job `preview` (không đổi trạng thái run): `docker compose -p sdlc-preview-<run8>` với file override do nền tảng
  sinh để mở `127.0.0.1:<cổng>` cho `service`; chờ `health_path`; ghi artifact `preview` (url, cổng, hạn tắt).
- Cổng lấy trong `SDLC_PREVIEW_PORTS` (mặc định 9100-9199); tối đa `SDLC_PREVIEW_MAX_RUNNING` (mặc định 2), quá thì tắt
  bản cũ nhất; tự tắt sau `SDLC_PREVIEW_TTL_MINUTES` (mặc định 60) và khi run kết thúc. Worker kiểm hạn mỗi lần poll PR.
- Preview có mạng ra ngoài như khi lập trình viên chạy app trên máy; chỉ mở trên 127.0.0.1, có giới hạn thời gian.
- Control room: nút Start preview / Try it / Stop, bảng tài khoản demo, trên trang run và ở cổng PR/Acceptance.

## P2: bước Acceptance

Sau Review, trước PR, khi dự án có `preview.e2e` và `stages.acceptance` khác `skip`:

1. QA Engineer (vai doing) viết test Playwright cho từng AC/EC của US trong `preview.e2e_paths`; tên test bắt đầu bằng
   mã tiêu chí (`AC-01 …`); dùng `BASE_URL` và tài khoản demo; chụp ảnh mỗi bước chính.
2. Bật preview, chạy `preview.e2e` trong `e2e_image`, cùng mạng với preview, `BASE_URL=http://<service>:<port>`, repo
   chỉ đọc, `/evidence` ghi được; lệnh ghi báo cáo JSON của Playwright vào `/evidence/results.json`.
3. Controller đọc báo cáo: mỗi test → tiêu chí (theo mã trong tên), trạng thái, lỗi, ảnh/video; lưu bằng chứng
   `.data/evidence/<run>/`; tài liệu `acceptance` (bảng tiêu chí → kết quả, ảnh).
4. `gated`: cổng **QC Acceptance**: link Try it, bằng chứng theo tiêu chí, tick; Accept → PR; Reject kèm lỗi → Developer
   sửa (task về READY với góp ý), rồi Review và Acceptance lại. `auto`: đạt hết thì qua PR, trượt thì về Developer.
