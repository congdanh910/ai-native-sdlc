# Việc cần đánh giá và làm sau

_Cập nhật: 2026-10-17. Mỗi mục ghi vì sao nó nằm đây và lúc nào nên làm. Việc đang làm nằm ở `PROGRESS.md`._

## 1. Cần bàn với người dùng trước

| # | Việc | Vì sao | Gợi ý |
|---|---|---|---|
| 1.1 | **Cấu hình AI provider và tài khoản**: model theo từng vai, tài khoản riêng cho nền tảng, model Codex theo dự án (đang lấy từ `~/.codex/config.toml`: `gpt-6-sol`, effort high) | Hiện dùng chung một tài khoản Claude với phiên làm việc của Claude Code nên dễ hết quota (16/10 đã hết, phải chuyển Discovery sang Codex) | Người dùng đã nói "để thảo luận sau"; gộp chung với 1.2 |
| 1.2 | **Cưỡng chế tách biệt ngữ cảnh** giữa các run và dự án | Hiện tách bằng dữ liệu đầu vào (JSON theo run, worktree riêng, phiên CLI không lưu). Nhưng Claude Read và Codex read-only vẫn *đọc được* file ở chỗ khác trên máy nếu tự tìm sang | Chạy mỗi lần gọi AI trong container chỉ mount thư mục của run, như bước validation; cần khi có nhiều khách hàng trên cùng máy |
| 1.3 | Bỏ hay giữ code Langflow/Qwen | Qwen đã tắt từ 16/10, code vẫn còn và vẫn có test | Giữ cho tới khi chốt 1.1 |
| 1.4 | Giữ hay xoá dự án thử `discovery-trial` (repo cục bộ `.data/demo-repos/discovery-trial`) | Đang dùng để xem kết quả D1–D6 | Xoá khi người dùng xem xong |

## 2. Discovery: chất lượng và tính dùng chung

| # | Việc | Vì sao |
|---|---|---|
| 2.1 | Chạy lại Extract trên file VMS và OCR sau các bản sửa ngày 16/10 (links, quyết định có tác động rõ, chia đôi lượt quá dài) | Kiểm thiết kế vẫn chung cho mọi dự án; hỏi người dùng trước vì tốn quota |
| 2.2 | So sánh Claude và Codex làm BA trên cùng một bộ tài liệu | Codex tách mục mịn hơn nhiều (76 mục từ 2 trang, Claude 63 mục cả file 11 trang) và không thấy hình của trang (bảng, sơ đồ) |
| 2.3 | PDF scan và ảnh khi BA chạy Codex | Hiện dừng và báo lỗi; cần OCR (ví dụ Tesseract) hoặc bắt buộc Claude cho loại nguồn này |
| 2.4 | Lưu kết quả từng lượt đọc ngay khi xong, chạy lại thì đọc tiếp | Job hỏng giữa chừng (hết quota, máy ngủ) thì đọc lại từ đầu |
| 2.5 | Vòng "Check the inventory again" có nên tính vào `max_rounds` | Trên run thử đã dùng 4 trên 5 vòng, phần lớn chỉ là vòng consolidate. Từ 17/10 PO xác nhận được vòng vượt giới hạn kèm lý do (event `discovery.round_beyond_limit`) |
| 2.6 | Đổi engine của một run đang mở ngay trên giao diện | 16/10 phải dùng script để ghim lại engine run 9735e5fd (có event `engines.repinned`) |
| 2.7 | Vai Discovery chỉnh trên UI | Người dùng: mặc định PO, BA, SA, TA, QC, DPO, Sales là đủ; "linh hoạt trên UI để sau" |
| 2.8 | Khách hàng tự trả lời câu hỏi trên nền tảng; đồng bộ câu hỏi với Jira/email; đọc ghi âm/video họp | Ngoài phạm vi theo thiết kế (mục 11), cần tài khoản và phân quyền |

## 3. Nền tảng và vận hành

| # | Việc | Vì sao |
|---|---|---|
| 3.1 | Nhiều worker chạy song song | Một worker nên job extract dài chặn preflight của dự án khác (16/10) |
| 3.2 | PR mô phỏng cho repo cục bộ không merge thật | Phải `git pull` tay vào `main` của repo thử thì run SRS/SDS mới thấy baseline/SRS |
| 3.3 | Test giao diện tự động (Playwright) cho các luồng chính | Lỗi bấm tab Sources/Requirements không mở được (từ D1, sửa 17/10) không có test nào bắt; máy đã có Chrome và Playwright trong scratchpad |
| 3.4 | Chia nhỏ bundle frontend (643 KB) | Cảnh báo của Vite |
| 3.5 | Giao diện tiếng Việt | Hiện UI tiếng Anh |
| 3.6 | Giai đoạn B7 | Ghi là "Planned" trong `poc-option-b/README.md`, chưa định nghĩa |

## 4. Việc cũ còn treo

| # | Việc |
|---|---|
| 4.1 | PR #1 cũ của `ai-native-inventory-demo` vẫn mở trên GitHub |
| 4.2 | Hai góp ý nhỏ của Code Reviewer dành cho story AUTH-US-02 Logout của attendance (code attendance đã reset ngày 16/10, làm lại khi tới story đó) |
