# Hướng dẫn review làn AIF

_15/10/2026 · branch `feature/option-b-v2` · bảng theo dõi: [plan doc](https://claude.ai/artifact/FhKU7DV93WYdVjve7fmsm5), mục "Theo dõi làn AIF"_

Làn AIF đưa AI-Native SDLC về quy trình AIF-SDLC của công ty. Trang này gom mọi thứ cần xem trong một lượt review: cái gì đã đổi, xem ở đâu, và điểm dừng trên dự án attendance.

## 1. Thay đổi theo bước

| # | Bước | Commit | Xem ở đâu |
|---|---|---|---|
| 1 | Mapping AIF-SDLC → AI-Native SDLC | `f2181fa`, `a09b280` | [aif-mapping.md](aif-mapping.md) |
| 2 | Run SRS của dự án | `aaaee76` | New request → Project SRS; `controller/app/project_docs.py`, `langflow/prompts/srs.md` |
| 3 | Run SDS của dự án | `3339641` | New request → Project SDS; `langflow/prompts/sds.md` |
| 4 | Artifact của US trong `specs/NNN-slug/` | `056325e` | Run tính năng có US; `controller/app/feature_docs.py` |
| 5 | Cổng Quality (QC duyệt test cases) | `9def70e` | Trạng thái `WAITING_TEST_APPROVAL`; khối "Test cases v…" |
| 6 | Checklist ở mọi cổng | `6f8ed6a` | Khối "Review checklist"; `controller/app/checklists.py` |
| 7 | Constitution trong repo | `1ae12ac` | Projects: dòng `constitution`; mọi prompt |
| 8 | Experience Sharing vào prompt + eval | `ad501d0` | `langflow/prompts/*.md`, mục "Lessons…"; kết quả eval ở mục 3 |
| 9 | Phân tích chéo trước và sau code | `69ea126` | Thread: `analysis.md`; `controller/app/analysis.py` |
| 10 | Chạy thật trên attendance | `8ca749c` (sửa lỗi gặp khi chạy) | Run `f0f534c4`, mục 2 |
| 11 | Tài liệu | `15f8167` và commit này | [huong-dan-dung-thu.md](huong-dan-dung-thu.md), [ui-design.md](ui-design.md) |

## 2. Review trên control room (attendance)

Control room: http://127.0.0.1:8770. Run SRS của attendance (`f0f534c4`, http://127.0.0.1:8770/#/runs/f0f534c4-a76e-42b4-b9cc-20163e3b6146) dừng ở cổng SRS, chờ anh/chị. SRS bản 1 có 4 vai (ADMIN, HR, MANAGER, EMPLOYEE), 4 feature (AUTH, EMP, CHK, RPT) với 24 US, 7 luồng nghiệp vụ; controller check pass.

Lần chạy đầu dừng ở NEEDS_HUMAN: Business Analyst (claude-readonly) trả `attributes` của thực thể dạng danh sách, contract cần chuỗi, cả 3 lần thử bị từ chối. Đã sửa bộ chuẩn hoá (`8ca749c`), restart và resume; lần hai qua.

1. **Cổng SRS** (Inbox → "Review SRS.md"). Đọc `SRS.md`:
   - §1.5 vai có đúng không (ADMIN, HR, MANAGER, EMPLOYEE theo brief nháp);
   - §2 thực thể và business rules (`BR-01`…);
   - §3 FR theo feature (`XX-FR-01`…);
   - §6 luồng nghiệp vụ;
   - §7 danh sách US (`XX-US-01`…) mà các run sau sẽ chọn.

   Checklist: controller kiểm tra đủ 8 mục và ID US không trùng; tick 3 mục của bạn. Muốn sửa thì **Edit** (controller kiểm tra lại định dạng) hoặc **Reject: rewrite** kèm lý do (BA nhận lý do và viết lại). Brief là bản nháp do Claude soạn từ AGENTS.md của repo và kế hoạch pilot; xem ở tab Thread → `brief.md`.
2. **PR SRS.md**: Accept mở draft PR trên `ai-native-attendance-check-demo`. Merge nếu đồng ý, rồi Projects → attendance → **Check again**.
3. **Run SDS**: New request → Project SDS, project attendance. Cổng SDS do Tech Lead duyệt; khung vàng liệt kê thực thể SRS chưa có trong §2.1 và US chưa có Purpose.
4. **Run US đầu tiên**: New request → Feature request, chọn US trong ô **User story**. Đi qua 4 cổng; ở mỗi cổng, xem file nào được ghi vào `specs/NNN-slug/` (tab Thread và branch của run).

Bước 3 và 4 chưa chạy: chúng cần SRS đã merge, mà đó là quyết định của anh/chị ở bước 1–2.

## 3. Eval sau khi đổi prompt

Cùng engine với baseline 14/10 (BA Qwen3 trên GX10, Tech Lead và QA Analyst claude-readonly), 5 case mỗi suite:

| Suite | Plan qua grounding lần đầu | File recall | File precision | Tiêu chí / case | Test case / case | Câu hỏi chặn |
|---|---|---|---|---|---|---|
| task-board, baseline `6eabf052` | 5/5 | 1.0 | 1.0 | 7–8 | 12–21 | 0 |
| task-board, sau đổi prompt `1b0fcfaf` | 5/5 | 1.0 | 1.0 | 16–20 (AC + EC) | 19–25 | 0 |
| inventory, baseline `131f0965` | 4/4 (1 case dừng vì câu hỏi chặn) | 1.0 | 1.0 | 8 | 13–20 | 1 |
| inventory, sau đổi prompt `c1af49da` | 5/5 | 1.0 | 0.95 | 10–16 (AC + EC) | 13–24 | 0 |

- Chất lượng plan không tụt; inventory còn tốt hơn: không còn case dừng vì câu hỏi chặn.
- Số tiêu chí gấp khoảng đôi vì spec giờ có Edge Cases và bỏ giới hạn 8 tiêu chí; số test case tăng theo.
- Precision 0.95: case `csv-export` sửa thêm `inventory/__main__.py` ngoài plan mẫu.
- Thời gian tới plan tăng (400–1700 giây so với 160–430). Một phần vì hai eval chạy cùng lúc qua một worker nên phải xếp hàng; một phần vì spec và test plan dài hơn. Chưa tách được hai phần này.

## 4. Giới hạn đã biết

- Run SRS và SDS mỗi lần viết lại cả file; sửa một phần thì dùng **Edit** ở cổng.
- Template mặc định dựng sẵn trong code theo template GRM; repo có `.specify/templates/` riêng thì chưa đọc.
- Phần AI của analyze là self-check của các vai và review của Code Reviewer; chưa có vai AI riêng chạy analyze.
- Verify (test_report.md), regression và Jira chưa làm, theo plan (giai đoạn E và sau).
- Một operator vẫn giữ mọi vai người; checklist ghi tên operator, chưa ghi người thật.
