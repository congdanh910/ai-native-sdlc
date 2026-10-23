# Điều phối nhiều story

_2026-10-23. Người dùng chốt: story B phụ thuộc A chỉ bắt đầu khi A đã merge; nhánh run theo kịp `main` bằng merge
`main` vào nhánh (không rebase); mặc định tự mở story tiếp theo, tối đa 2 run mở; Tech Lead duyệt backlog. Làm theo
thứ tự B1 → B2 → B3._

## Vấn đề

Mỗi story là một run feature tạo tay; dự án thật có hàng chục story (attendance: 43). SRS không ghi phụ thuộc giữa các
story, SDS §5 chỉ có mục đích. Hai story chạy cùng lúc sẽ đụng nhau ở:

- file tài liệu do nền tảng sinh: `specs/<feature>/spec.md|plan.md|tasks.md|test_cases.md|sdlc.yaml`, dòng link trong
  SRS, số TC đánh liên tục trong cả file;
- migration: hai nhánh cùng tạo `0003_*` trên `0002` thành hai head; git không báo xung đột, chạy migration mới lỗi;
- code của chính các story.

## B1. Backlog theo phụ thuộc

Tab **Backlog** của dự án (trang Projects → Backlog).

- **Propose order**: Tech Lead AI (prompt `backlog.md`) đọc SRS (vai, entity, flow, story) và SDS (domain model, bảng
  API), trả về mọi story theo thứ tự nên làm, mỗi story `depends_on` và lý do. Có backlog cũ thì giữ những gì không cần
  đổi; ghi chú của Tech Lead (`feedback`) được trả lời.
- **Controller kiểm** (không AI): mỗi story của SRS đúng một lần, không id lạ, không tự phụ thuộc, không vòng phụ thuộc
  (nêu vòng). Đợt (wave) = 1 + đợt lớn nhất của các story nó phụ thuộc.
- **Tech Lead sửa**: thứ tự (lên/xuống), phụ thuộc, **Hold** (không mở), **Done outside** (đã có từ Foundation hay làm
  tay, kèm lý do). Mọi lần lưu đưa backlog về nháp; **Approve** chỉ khi không còn lỗi kiểm.
- **Trạng thái mỗi story** (suy ra, không lưu): `delivered` (run gần nhất COMPLETED, tức PR đã merge, hoặc Done
  outside), `running` (run đang mở), `held`, `blocked` (nêu story chặn; Foundation của dự án chưa merge cũng chặn),
  `ready`. Run CLOSED_UNMERGED/FAILED/CANCELLED thì story về ready.
- SRS đổi: story mới hiện ở **Not in the backlog**, story đã bỏ khỏi SRS bị đánh dấu; Approve cần xếp hết.
- **Start**: story ready mở run feature với tiêu đề, vấn đề (statement) và outcome gợi ý từ SRS/SDS
  (`outcomes.suggest`); run dừng ở mọi cổng như run tạo tay.

Lưu trong bảng `backlogs` (một dòng mỗi dự án: items, ghi chú, trạng thái nháp/duyệt, người duyệt, commit SRS); sự kiện
`backlog.*` ghi lịch sử.

## B2. Tự mở story tiếp theo, nhiều run song song

```yaml
delivery:
  auto_start: true      # mở story ready kế tiếp khi còn chỗ
  max_open_runs: 2      # run feature đang mở của dự án
```

- Worker (vòng poll) mở story ready có thứ hạng cao nhất khi số run feature đang mở < `max_open_runs` và backlog đã
  duyệt. Idempotency key `backlog:<dự án>:<story>:<lần>` nên không mở trùng.
- Nhiều worker: `SDLC_WORKER_CONCURRENCY` (mặc định 1); mỗi run chỉ một job chạy cùng lúc; hạn mức engine giữ nguyên;
  preview đã có `SDLC_PREVIEW_MAX_RUNNING`. Bộ nhớ Docker: BACKLOG 3.18.
- **Chặn chồng file**: plan đã duyệt biết `modify/create`. Trùng file với run đang mở khác thì cổng plan báo, Tech Lead
  chọn **Wait for <run>** (run giữ ở READY tới khi run kia merge) hoặc **Proceed**.

## B3. Theo kịp `main`, xử lý xung đột

- `main` mới (poll PR, project check) → mỗi run feature đang mở có nhánh nhận job `update_branch`, chạy giữa các task.
  Merge `main` vào nhánh (như Update branch của GitHub): không force-push, commit mà bằng chứng, preview, red check,
  `task_base` trỏ tới vẫn còn.
- Sau cập nhật: validation chạy lại; run đã qua Acceptance thì Acceptance chạy lại; cổng QC/PR ghi "đã cập nhật theo
  `main`" và kết quả mới.
- Xung đột, ba lớp:
  1. File tài liệu nền tảng sinh: dựng lại section của story trên bản của `main` (`upsert_section` tất định), không AI.
  2. Migration hai head: nền tảng phát hiện, task cho Developer nối migration lên head của `main`.
  3. Code: task **Resolve conflicts with main** (Tn+1 như task sửa lỗi) với file xung đột và plan của story kia; qua
     validation, review, Acceptance. Không giải được thì NEEDS_HUMAN kèm danh sách xung đột.
- Hai PR cùng mở: PR merge trước thắng, PR kia cập nhật như trên. Không bao giờ tự merge.
