# Việc cần đánh giá và làm sau

_Cập nhật: 2026-10-21. Mỗi mục ghi vì sao nó nằm đây và lúc nào nên làm. Việc đang làm nằm ở `PROGRESS.md`._

## 1. Cần bàn với người dùng trước

| # | Việc | Vì sao | Gợi ý |
|---|---|---|---|
| 1.1 | **Cấu hình AI provider và tài khoản**: model theo từng vai, tài khoản riêng cho nền tảng, model Codex theo dự án (đang lấy từ `~/.codex/config.toml`: `gpt-6-sol`, effort high) | Hiện dùng chung một tài khoản Claude với phiên làm việc của Claude Code nên dễ hết quota (16/10 đã hết, phải chuyển Discovery sang Codex) | Người dùng đã nói "để thảo luận sau"; gộp chung với 1.2 |
| 1.2 | **Cưỡng chế tách biệt ngữ cảnh** giữa các run và dự án | Hiện tách bằng dữ liệu đầu vào (JSON theo run, worktree riêng, phiên CLI không lưu). Nhưng Claude Read và Codex read-only vẫn *đọc được* file ở chỗ khác trên máy nếu tự tìm sang | Chạy mỗi lần gọi AI trong container chỉ mount thư mục của run, như bước validation; cần khi có nhiều khách hàng trên cùng máy |
| 1.3 | Bỏ hay giữ code Langflow/Qwen | Qwen đã tắt từ 16/10, code vẫn còn và vẫn có test | Giữ cho tới khi chốt 1.1 |

## 2. Discovery: chất lượng và tính dùng chung

| # | Việc | Vì sao |
|---|---|---|
| 2.1 | Chạy lại Extract trên file VMS và OCR sau các bản sửa ngày 16/10 (links, quyết định có tác động rõ, chia đôi lượt quá dài) | Kiểm thiết kế vẫn chung cho mọi dự án; hỏi người dùng trước vì tốn quota |
| 2.2 | So sánh Claude và Codex làm BA trên cùng một bộ tài liệu | Codex tách mục mịn hơn nhiều (76 mục từ 2 trang, Claude 63 mục cả file 11 trang) và không thấy hình của trang (bảng, sơ đồ) |
| 2.3 | PDF scan và ảnh khi BA chạy Codex | Hiện dừng và báo lỗi; cần OCR (ví dụ Tesseract) hoặc bắt buộc Claude cho loại nguồn này |
| 2.4 | ~~Lưu kết quả từng lượt đọc ngay khi xong, chạy lại thì đọc tiếp~~ Đã làm 19/10 trong một job (`joblog.resumable`): lần thử lại dùng lại các phần đã đọc. Còn lại: job hỏng hẳn (hết 3 lần) rồi bấm Extract again thì vẫn đọc lại | Hết quota giữa chừng hay dừng hẳn thì vẫn mất phần đã đọc |
| 2.6 | Đổi engine của một run đang mở ngay trên giao diện | 16/10 phải dùng script để ghim lại engine run 9735e5fd (có event `engines.repinned`) |
| 2.7 | Vai Discovery chỉnh trên UI | Người dùng: mặc định PO, BA, SA, TA, QC, DPO, Sales là đủ; "linh hoạt trên UI để sau" |
| 2.8 | Khách hàng tự trả lời câu hỏi trên nền tảng; đồng bộ câu hỏi với Jira/email; đọc ghi âm/video họp | Ngoài phạm vi theo thiết kế (mục 11), cần tài khoản và phân quyền |
| 2.9 | Hướng dẫn riêng theo vai cho từng dự án, ví dụ `.sdlc/prompts/business_analyst.md` trong repo dự án: nối sau hướng dẫn lõi, không đổi được định dạng đầu ra, tính vào prompt version của run, sửa qua PR | Người dùng hỏi về tuỳ chỉnh prompt (17/10); hiện chỉ có AGENTS.md/constitution chung cho mọi vai. Đây là cách các công cụ lớn đang làm (CLAUDE.md, AGENTS.md, Copilot instructions) |
| 2.10 | Discovery mới kế thừa baseline cũ (bảng requirement, quyết định, nguồn) thay vì bắt đầu từ trống | Hiện sửa requirement sau khi baseline đã merge thì phải mở discovery mới và tải lại nguồn (17/10) |
| 2.11 | Ngôn ngữ tài liệu: `documents.language` (auto / vi / en) trong project.yaml, ô "Document language" khi tạo run, ngôn ngữ truyền vào mọi prompt; khung template giữ tiếng Anh, có thể thêm tiêu đề song ngữ (`documents.headings: bilingual`) | Hiện nội dung tự theo ngôn ngữ nguồn nhưng chỉ prompt Discovery dặn rõ; SRS/SDS/spec/plan có thể ra tiếng Anh tuỳ engine. Người dùng: "tính sau" (17/10) |

## 3. Nền tảng và vận hành

| # | Việc | Vì sao |
|---|---|---|
| 3.1 | Nhiều worker chạy song song | Một worker nên job extract dài chặn preflight của dự án khác (16/10) |
| 3.2 | PR mô phỏng cho repo cục bộ không merge thật | Phải `git pull` tay vào `main` của repo thử thì run SRS/SDS mới thấy baseline/SRS |
| 3.3 | Test giao diện tự động (Playwright) cho các luồng chính | Lỗi bấm tab Sources/Requirements không mở được (từ D1, sửa 17/10) không có test nào bắt; máy đã có Chrome và Playwright trong scratchpad |
| 3.4 | Chia nhỏ bundle frontend (643 KB) | Cảnh báo của Vite |
| 3.5 | Giao diện tiếng Việt | Hiện UI tiếng Anh |
| 3.6 | Giai đoạn B7 | Ghi là "Planned" trong `poc-option-b/README.md`, chưa định nghĩa |
| 3.7 | **Validation gọi ra ngoài có kiểm soát**: `network: {allow: [host...]}` và `secrets: [TÊN]` theo từng lệnh validation; proxy trong mạng của lần validation chỉ cho đi tới host được phép và ghi log mọi request; secret (token sandbox) lưu trong kho secret của nền tảng, không nằm trong repo, chỉ truyền vào đúng lệnh đó, không đưa cho phiên AI viết code; lệnh có mạng chạy một lần trước khi mở PR (không chạy trong vòng red check/sửa code), kết quả hiện ở cổng PR; Onboarding và cổng Tech Lead thấy rõ các host được mở | Hiện validation không ra Internet (an toàn). Tích hợp thường dùng service giả trong `environment.services` (WireMock, LocalStack, MinIO, Mailpit…), đủ cho attendance; chỉ làm khi có dự án thật sự cần gọi sandbox bên ngoài (21/10) |
| 3.8 | **Preview và regression của `main`**: một bản "sản phẩm hiện tại" dựng từ `main` (nút Open preview of main ở trang dự án) để PO và khách xem các US đã ghép; chạy toàn bộ bộ E2E trên `main` sau mỗi merge để bắt US mới làm hỏng US cũ; mở preview ra ngoài (tunnel) cần đăng nhập và bảo mật nên tính sau. Deploy staging/production vẫn do người làm | Người dùng chọn làm trước preview theo run, E2E có bằng chứng và cổng QC Acceptance; phần `main` để sau (21/10) |

## 4. Việc cũ còn treo

| # | Việc |
|---|---|
| 4.1 | PR #1 cũ của `ai-native-inventory-demo` vẫn mở trên GitHub |
| 4.2 | Hai góp ý nhỏ của Code Reviewer dành cho story AUTH-US-02 Logout của attendance (code attendance đã reset ngày 16/10, làm lại khi tới story đó) |
