# Tiến độ và bàn giao

_Cập nhật: 2026-10-16_

## Việc đang làm: Discovery (tài liệu thô → baseline → SRS và SDS)

Thiết kế: `poc-option-b/docs/discovery-design.md`. Tracker: bảng Discovery trong plan doc
https://claude.ai/code/artifact/770112ad-5c83-4811-90cc-30845b1619c4 (cột Trạng thái, cập nhật sau mỗi bước).

| Bước | Trạng thái | Ghi chú |
|---|---|---|
| D1 Sources | Chờ người dùng duyệt | 88c47ce |
| D2 Extract | Chờ người dùng duyệt | 5e19e25, d583472, 8f57700; chạy thật trên 3 PDF, mọi mục có trang nguồn |
| D3 Questions, Decisions, Re-extract | Chờ người dùng duyệt | xem dưới |
| D4 Baseline + PR `requirements/` | Chưa bắt đầu | |
| D5 SRS từ baseline + phụ lục truy vết | Chưa bắt đầu | |
| D6 Options + SDS (SA chọn phương án) | Chưa bắt đầu | |

### D3: đã làm

- Câu hỏi: gaps của mỗi vòng thành `clarifications` (Q-001…, vai theo `discovery.question_owners`, không lặp,
  tối đa `max_questions_per_round`, câu chặn trước). Trả lời / hoãn / rút / mở lại / chuyển vai; trả lời tạo
  quyết định `accepted`. Nút Workshop list (Markdown theo vai, không gọi AI).
- Biên bản: nguồn `purpose=minutes`, prompt `langflow/prompts/minutes.md` rút quyết định `proposed`; vai sở hữu
  Accept/Reject; Accept thì câu hỏi liên quan thành answered.
- Extract again: chỉ đọc nguồn và biên bản mới (kết quả từng nguồn lưu trong `data.chunks` của inventory); REQ id
  không đổi giữa các vòng; consolidate áp quyết định (`updates`: confirmed / changed / out_of_scope, trích
  `decision:D-00N`). Không có gì mới thì chặn. Inventory cũ (trước D3) được dựng lại theo nguồn, không đọc lại PDF.
- Tài liệu `questions.md`, `decisions.md` trong thread; tab Questions, Decisions; panel CLARIFYING hiện số câu
  mở theo vai. Code: `controller/app/discovery/questions.py`, migration 0008.
- Chạy thật trên `discovery-trial` (run 470903ac): vòng 3 chỉ 1 lượt consolidate (~2 phút), 67 mục giữ id,
  14 câu hỏi (PO 8, TA 4, BA 1, Sales 1).

### D3: còn lại

- Người dùng trả lời vài câu trên tab Questions của run 470903ac (có thể thêm một biên bản), rồi bấm Extract again
  để chạy hết một vòng thật.
- Chưa làm: thẻ "Questions for you" trong Inbox gom theo vai (thiết kế mục 9) — làm cùng D4 nếu cần.

## Trạng thái nền tảng

- `.env`: `SDLC_TEAM_MODE=claude-readonly`, `SDLC_QWEN_ENABLED=false`, `SDLC_DOING_MODE=cli`, mọi vai suy nghĩ
  trên `claude-readonly`. Codex CLI cho vai viết code (model trong `~/.codex/config.toml`).
- Dữ liệu: đã clean slate ngày 16/10. Có dự án `attendance` (chưa run) và 3 dự án thử Discovery:
  `discovery-trial` (đạo văn, run 470903ac, inventory v2: 67 mục), `discovery-trial-2` (VMS, d546915a: 155 mục),
  `discovery-trial-3` (OCR, 70ef00a6: 281 mục). Cả 3 ở CLARIFYING. Người dùng chưa quyết giữ hay xoá.
- Test lần cuối: 202 pass (bỏ test docker, test đó pass khi chạy riêng).

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

Sau khi người dùng chạy thử một vòng D3: D4 Baseline (checklist cổng, PO chốt, commit `requirements/` kèm file
thô, PR), cập nhật tracker sau mỗi bước.
