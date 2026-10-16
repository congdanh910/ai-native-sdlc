# Quyết định

Quyết định bền vững của dự án và lý do. Quyết định thiết kế chi tiết hơn nằm trong `AI_Native_SDLC_Option_B.md`
(mục Revision notes, Open questions) và `poc-option-b/docs/discovery-design.md` mục 12.

| Ngày | Quyết định | Lý do |
|---|---|---|
| 2026-10-13 | Web control room là giao diện duy nhất cho việc của team; CLI chỉ để vận hành | Một người vận hành, không cần nhớ lệnh |
| 2026-10-13 | QA dùng AI viết test trước (red check), rồi Developer viết code | Test chứng minh đúng tiêu chí, không viết theo code |
| 2026-10-13 | Không bao giờ tự merge, không deploy; mọi spec, plan, test, PR có người duyệt | An toàn, người chịu trách nhiệm ở mọi cổng |
| 2026-10-14 | Gọi AI qua CLI của subscription (Claude Code, Codex), không dùng API key trả tiền | Không phát sinh chi phí; con số "$" CLI báo chỉ là ước tính theo giá API |
| 2026-10-14 | Chỉ thêm đúng file người dùng yêu cầu vào repo dự án của họ; hỏi trước khi thêm | Lần onboarding attendance bị đẩy thừa code mẫu, phải revert (58514e2) |
| 2026-10-15 | Theo quy trình công ty AIF-SDLC: SRS/SDS theo template công ty, artifact `specs/NNN-slug/`, checklist 3 nhóm ở mọi cổng, Quality gate cho test case, `constitution.md` | Khớp quy trình thật của công ty |
| 2026-10-15 | SRS và SDS là bước cấp dự án trước mọi feature run; mỗi run làm một user story trong SRS §7 | Feature truy vết về SRS |
| 2026-10-15 | Không hardcode: timeout, retry, giới hạn để trong `.sdlc/project.yaml` (theo dự án) hoặc `.env` (nền tảng), có mặc định | Mỗi dự án khác nhau; hằng số ẩn gây bất ngờ |
| 2026-10-15 | Code Reviewer chạy trên `claude-readonly` (590c619) | Qwen bỏ sót lỗi kiến trúc |
| 2026-10-15 | Jira để sau; chỉ làm UI control room | Tập trung |
| 2026-10-16 | Bỏ Qwen: mọi vai suy nghĩ dùng `claude-readonly`; `SDLC_QWEN_ENABLED=false`; GX10 không còn cần | Chất lượng và vận hành đơn giản hơn |
| 2026-10-16 | Dùng một tài khoản Claude chung (cùng tài khoản phiên làm việc); cấu hình AI provider/tài khoản bàn sau | Người dùng chọn |
| 2026-10-16 | Discovery phải chung cho mọi dự án; 3 PDF chỉ là bộ thử; mục tiêu là hiểu requirement để viết SRS/SDS | Người dùng nhấn mạnh hai lần |
| 2026-10-16 | File thô của Discovery được commit vào repo (`requirements/sources/`), `commit_sources: true` mặc định | Tài liệu hợp đồng đã được audit trước khi tải lên |
| 2026-10-16 | Vai mặc định của Discovery: PO, BA, SA, TA, QC, DPO, Sales; UI linh hoạt hơn để sau | Đủ cho giai đoạn này |
| 2026-10-16 | Bảng requirement: mỗi mục ghi trang của file (số riêng của tài liệu trong ngoặc); chỉ lấy requirement của sản phẩm, không lấy phần nói về chính tài liệu | Lần thử đầu lẫn "mục đích tài liệu" và số slide không mở được |
| 2026-10-16 | Mỗi lượt BA đọc 10 trang PDF (`pages_per_read`, tối đa 20); lượt hỏng vì câu trả lời quá dài thì chia đôi, không thử lại cùng cỡ | 20 trang dày vượt giới hạn output của CLI, hỏng 3 lần liền |
| 2026-10-16 | Từ D3 đến D6 chỉ thử trên file đạo văn; chạy lại cả 3 file khi xong D6, hỏi trước | Tiết kiệm hạn mức subscription |
| 2026-10-16 | Extract again chỉ đọc nguồn và biên bản mới; REQ id không đổi giữa các vòng; không có gì mới thì không chạy | Tiết kiệm hạn mức; câu trả lời và quyết định trỏ vào REQ id phải còn đúng |
| 2026-10-16 | Quyết định từ biên bản là `proposed` cho tới khi vai sở hữu duyệt; câu trả lời trực tiếp là `accepted` | AI không tự chốt thay người |
