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
| D4 Baseline + PR `requirements/` | Chờ người dùng duyệt | 2f83992 |
| D5 SRS từ baseline + phụ lục truy vết | Chờ người dùng duyệt | fd22a94 |
| D6 Options + SDS (SA chọn phương án) | Chờ người dùng duyệt | xem dưới |

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

### D4–D6: đã làm

- D4: PO soạn baseline (nêu phạm vi giai đoạn) → cổng baseline: 7 mục controller kiểm (không còn câu chặn / mâu
  thuẫn mở, không còn quyết định chờ duyệt, inventory đã áp mọi quyết định, inventory không lỗi, feature trong phạm vi
  có function, NFR có số hoặc câu hỏi đã trả lời/hoãn) + 3 mục PO tick → commit `requirements/` (baseline.md,
  baseline.json, inventory.md, questions.md, decisions.md, sources.md, sources/) → draft PR. Quyết định ghi trực
  tiếp (ví dụ đưa REQ ra ngoài phạm vi); Inbox có thẻ "Questions for you" theo vai. Code:
  `controller/app/discovery/baseline.py`.
- D5: form SRS có "Build on the discovery baseline"; preflight đọc `requirements/baseline.json` (chưa merge thì
  NEEDS_HUMAN); mọi phần tử SRS có `refs` REQ; SRS.md có "Appendix A. Requirement traceability"; cổng SRS chặn khi có
  REQ trong phạm vi chưa dùng hoặc REQ lạ (option để cho SDS).
- D6: run SDS có bước Options (`stages.options: gated|skip`, mặc định gated): Architect so sánh 2–4 phương án
  (`options.md`, prompt `langflow/prompts/options.md`), Solution Architect chọn kèm lý do hoặc yêu cầu so sánh lại;
  SDS §9 mục "Architecture Option" ghi phương án và lý do, cổng SDS kiểm điều đó.

### Còn lại

- Chạy thật một vòng trên file đạo văn: Discovery → câu hỏi/quyết định → baseline → PR → SRS từ baseline → Options →
  SDS (đang làm, xem mục Bước tiếp theo).

## Trạng thái nền tảng

- `.env`: `SDLC_TEAM_MODE=claude-readonly`, `SDLC_QWEN_ENABLED=false`, `SDLC_DOING_MODE=cli`, mọi vai suy nghĩ
  trên `claude-readonly`. Codex CLI cho vai viết code (model trong `~/.codex/config.toml`).
- Dữ liệu: đã clean slate ngày 16/10. Chỉ còn dự án `attendance` (chưa run). Ba dự án thử Discovery
  (`discovery-trial*`, kết quả: đạo văn 67 mục, VMS 155, OCR 281) đã xoá hẳn theo yêu cầu người dùng tối 16/10;
  để thử tiếp D3–D6 cần tạo lại một dự án thử (repo cục bộ có `.sdlc/project.yaml` với `project:` trùng key).
- Test lần cuối: 207 pass (bỏ test docker, test đó pass khi chạy riêng).

## Việc treo, chờ người dùng

- Duyệt D1, D2, D3 trên tracker.
- GX10 (`cdp@118.69.64.152:2222`): cả hai container của dự án đã dừng (16/10), restart policy `no`, volume giữ
  nguyên: `ai-native-sdlc-ollama-30b`, `ai-native-sdlc-ollama-14b`. Bật lại: `sudo docker start <tên>`.
  Không đụng container của dự án khác trên GX10.
- Bàn sau: cấu hình AI provider/tài khoản (model theo vai, tài khoản riêng cho nền tảng).
- PR #1 cũ của inventory-demo vẫn mở trên GitHub.

## Bài học vận hành

- Mac ngủ thì job dừng theo (worker giờ giữ `caffeinate` trước khi nhận job; tắt bằng `SDLC_KEEP_AWAKE=false`).
- Một worker: các job chạy lần lượt, job extract dài chặn preflight của dự án khác.
- Lượt đọc PDF dày tốn nhiều hạn mức subscription (10 trang ~12 phút); test trên file nhỏ trước.

## Bước tiếp theo

Chạy thử thật cả luồng trên một dự án thử cục bộ mới với file đạo văn, dừng ở các cổng cho người dùng xem; người
dùng kiểm tra một lần toàn bộ D1–D6 (đã thống nhất 16/10).
