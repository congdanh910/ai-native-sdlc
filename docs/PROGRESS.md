# Tiến độ và bàn giao

_Cập nhật: 2026-10-16_

## Việc đang làm: Discovery (tài liệu thô → baseline → SRS và SDS)

Thiết kế: `poc-option-b/docs/discovery-design.md`. Tracker: bảng Discovery trong plan doc
https://claude.ai/code/artifact/770112ad-5c83-4811-90cc-30845b1619c4 (cột Trạng thái, cập nhật sau mỗi bước).

| Bước | Trạng thái | Ghi chú |
|---|---|---|
| D1 Sources | Chờ người dùng duyệt | 88c47ce |
| D2 Extract | Chờ người dùng duyệt | 5e19e25, d583472, 8f57700; chạy thật trên 3 PDF, mọi mục có trang nguồn |
| D3 Questions, Decisions, Re-extract | **Đang làm** | xem dưới |
| D4 Baseline + PR `requirements/` | Chưa bắt đầu | |
| D5 SRS từ baseline + phụ lục truy vết | Chưa bắt đầu | |
| D6 Options + SDS (SA chọn phương án) | Chưa bắt đầu | |

### D3: đã làm (chưa commit)

- `models.py`: cột discovery trên `clarifications` (kind, owner_role, status, why, suggestion, items, citations,
  round, answered_by, status_reason); `sources.purpose` (requirements / minutes); bảng `decisions`.
- Migration `controller/migrations/versions/0008_discovery_questions.py`; `decisions` thêm vào TABLES của
  `controller/tests/integration/conftest.py`.

### D3: còn lại (kế hoạch đã chốt)

1. Sau mỗi lượt extract: chuyển `gaps` thành câu hỏi (`clarifications`), gán vai theo `discovery.question_owners`,
   không trùng câu đã có, tối đa `max_questions_per_round` (câu chặn trước).
2. Trả lời / hoãn / rút / chuyển vai câu hỏi (API + tab Questions nhóm theo vai); trả lời tạo quyết định
   `accepted` (basis answer). Danh sách câu hỏi cho workshop (Markdown theo vai, không cần AI).
3. Biên bản: nguồn `purpose=minutes`; prompt mới `minutes.md` rút quyết định `proposed`; vai sở hữu duyệt.
4. Extract again: chỉ đọc nguồn mới (lưu kết quả từng nguồn trong `data` của inventory); **REQ id giữ nguyên**
   giữa các vòng (khớp theo type/parent/name/alias, số mới nối tiếp); consolidate nhận quyết định và trả
   `updates` (status confirmed/changed/out_of_scope, text) trích dẫn `decision:D-00N`.
5. Tài liệu `questions.md`, `decisions.md` trong thread; tab Decisions; test; thử thật trên run đạo văn
   (`discovery-trial`, run 470903ac): người dùng trả lời vài câu, rồi Extract again (chỉ 1 lượt consolidate).

## Trạng thái nền tảng

- `.env`: `SDLC_TEAM_MODE=claude-readonly`, `SDLC_QWEN_ENABLED=false`, `SDLC_DOING_MODE=cli`, mọi vai suy nghĩ
  trên `claude-readonly`. Codex CLI cho vai viết code (model trong `~/.codex/config.toml`).
- Dữ liệu: đã clean slate ngày 16/10. Có dự án `attendance` (chưa run) và 3 dự án thử Discovery:
  `discovery-trial` (đạo văn, run 470903ac, inventory v2: 67 mục), `discovery-trial-2` (VMS, d546915a: 155 mục),
  `discovery-trial-3` (OCR, 70ef00a6: 281 mục). Cả 3 ở CLARIFYING. Người dùng chưa quyết giữ hay xoá.
- Test lần cuối: 199 pass (bỏ test docker, test đó pass khi chạy riêng).

## Việc treo, chờ người dùng

- Duyệt D1, D2 trên tracker; xem tab Requirements của 3 run thử.
- Giữ hay xoá 3 dự án `discovery-trial*`.
- GX10 (`cdp@118.69.64.152:2222`): container `ai-native-sdlc-ollama-14b` (qwen3:14b, port 11436) vẫn chạy, chưa
  quyết; `ai-native-sdlc-ollama-30b` đã dừng (khởi động lại: `sudo docker start ai-native-sdlc-ollama-30b`).
  Không đụng container của dự án khác trên GX10.
- Bàn sau: cấu hình AI provider/tài khoản (model theo vai, tài khoản riêng cho nền tảng).
- PR #1 cũ của inventory-demo vẫn mở trên GitHub.

## Bài học vận hành

- Mac ngủ thì job dừng theo (worker giờ giữ `caffeinate` trước khi nhận job; tắt bằng `SDLC_KEEP_AWAKE=false`).
- Một worker: các job chạy lần lượt, job extract dài chặn preflight của dự án khác.
- Lượt đọc PDF dày tốn nhiều hạn mức subscription (10 trang ~12 phút); test trên file nhỏ trước.

## Bước tiếp theo

Hoàn thành D3 theo danh sách trên, cập nhật tracker D3 = Chờ duyệt, rồi D4.
