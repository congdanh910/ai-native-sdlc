# Control room — UI design

_Mockup đã duyệt ngày 14/10/2026: [canvas trên claude.ai](https://claude.ai/artifact/EdeSnqmM2HNHH4XjMN75Na) · code: `frontend/`_

Control room là nơi duy nhất người làm việc với đội AI. Thiết kế theo kiểu Linear / Vercel / GitHub Actions: nền sáng trung tính, chữ Geist, một màu nhấn xanh, màu trạng thái rõ, mọi quyết định đặt ngay nơi cần quyết định.

## Nguyên tắc

1. **Việc chờ bạn đi trước.** Inbox là trang mặc định; mỗi việc là một thẻ hành động gom theo vai người (Code Owner, Tech Lead, Product Owner, Platform Admin).
2. **Quyết định nằm ở đầu trang run.** Khối quyết định có viền xanh, ghi rõ vai bạn đang đóng và đúng phiên bản bạn duyệt (id + SHA-256).
3. **Thấy được trước khi duyệt.** Plan được duyệt qua ma trận tiêu chí × task × test; case `expected pass` tô vàng, tiêu chí thiếu task hoặc test gắn nhãn đỏ.
4. **Mỗi bước để lại bằng chứng.** Dòng thời gian task cho thấy vai, CLI, commit, thời gian, và lý do khi một bước bị từ chối.
5. **Không chặn bằng UI những gì backend đã chặn.** Mọi luật (grounding, red check, path guard) ở controller; UI chỉ hiển thị và gửi quyết định.

## Màn hình

| Màn hình | Đường dẫn | Nội dung chính |
| --- | --- | --- |
| Inbox | `#/` | Thẻ theo vai, chỉ số đọc từ run (CI, review, số task pass, AC có test; với plan: grounding, cảnh báo, expected pass), nút **Open and decide**; bật thông báo trình duyệt |
| New request | `#/new` | Form intent.md (Title, Problem, Desired outcome, …) và khung **Draft with AI** bên phải |
| Runs | `#/runs` | Bảng run, trạng thái, đang chờ vai nào; tuỳ chọn hiện run thí nghiệm của eval |
| Run | `#/runs/<id>` | Pipeline 8 chặng (Intent, Spec, Cổng 1, Plan, Cổng 2, Coding, Review, Cổng 3), khối quyết định, tab Tasks / Plan / Thread / Diff / Events / Jobs & decisions, cột phải: PR, độ phủ tiêu chí, thông tin run |
| Projects | `#/projects` | Thẻ dự án với kết quả check (file onboarding, validation trong container, quyền GitHub), form thêm dự án |
| Evals | `#/evals`, `#/evals/<id>` | Chọn engine theo vai, chạy bộ eval, xu hướng grounding lần đầu, thẻ chỉ số so với baseline, từng case |
| Reports | `#/reports` | Thẻ số liệu theo vai, mức dùng theo engine, bảng các eval |
| Health | `#/health` | Kết quả kiểm tra sẵn sàng, cập nhật mỗi 10 giây |

Khối quyết định trên trang run thay đổi theo việc đang chờ:

| Việc chờ | Thao tác |
| --- | --- |
| Câu hỏi chặn | Trả lời từng câu → Send answers |
| Duyệt spec | Accept · Edit (lưu phiên bản mới) · Reject: rewrite · Reject: cancel run |
| Duyệt plan | Approve · Edit JSON (lưu phiên bản mới) · Reject: replan · Reject: cancel run |
| Pull request | Open PR · Request changes · Review again · Close without merge (PR giả lập: Record simulated merge) |
| Needs human | Let QA update the tests · Keep the tests (khi có tranh chấp) · Resume · Replan · Cancel run |
| Phân loại lỗi | Category · Stage · Role · Note → Save classification |
| Stage manual | Start stage |

## Design token

Định nghĩa trong `frontend/src/index.css`; component chỉ dùng tên token, nên đổi chế độ sáng/tối không cần sửa component.

| Token | Sáng | Tối | Dùng cho |
| --- | --- | --- | --- |
| `bg` | `#f7f7f5` | `#0f1013` | Nền trang |
| `panel` | `#ffffff` | `#16181d` | Thẻ, ô nhập |
| `sunken` | `#f2f2ef` | `#1d2026` | Ô số liệu, hover |
| `line` | `#e4e4df` | `#2a2d35` | Viền |
| `ink` / `ink-2` / `muted` | `#16181d` / `#3a3f4a` / `#5f6470` | `#ededea` / `#c9cbd1` / `#9a9fab` | Chữ chính, phụ, mờ |
| `accent` | `#2448e8` | `#7c93ff` | Màu nhấn, link, cổng duyệt |
| `good` | `#15803d` | `#4ade80` | Pass, xanh |
| `warn` | `#9a4a0a` | `#fdba74` | Red check như mong đợi, cảnh báo |
| `flag` | `#8a5a00` | `#facc15` | `expected pass` |
| `bad` | `#b91c1c` | `#f87171` | Lỗi, bị từ chối, `NEEDS_HUMAN` |
| `violet` | `#5b2db0` | `#c4b5fd` | Product Owner, engine khác Qwen trong eval |

Chữ: **Geist** (giao diện) và **Geist Mono** (id, commit, đường dẫn), đóng gói sẵn, không cần mạng. Bo góc 8–12 px, viền 1 px, không đổ bóng trừ ⌘K.

## Công nghệ

React 19 · TypeScript 5.9 · Vite 8 · Tailwind 4 · TanStack Query (tự cập nhật: 2 giây khi run đang chạy, 5–30 giây ở trang khác) · wouter (đường dẫn dạng `#/…`) · lucide-react (icon) · sonner (toast) · cmdk (⌘K) · react-markdown (tài liệu trong thread).

Bản build được commit trong `controller/web/dist/` và FastAPI phục vụ ở `/` và `/assets`, nên chạy không cần Node. Khi sửa giao diện:

```bash
cd frontend
npm ci
npm run dev     # http://127.0.0.1:5173, proxy /api tới controller đang chạy
npm run build   # kiểm tra kiểu rồi ghi controller/web/dist (commit lại)
```

## Còn có thể làm

- Giao diện tiếng Việt (hiện là tiếng Anh, khớp thông báo của backend).
- Cập nhật thời gian thực bằng SSE thay cho hỏi lại định kỳ.
- Tách bundle (hiện một file JS khoảng 600 kB, 180 kB gzip).
