# Mapping AIF-SDLC vào AI-Native SDLC

> Bước 1 của làn AIF. Nguồn: `aif-sdlc.md`, `aif-review-checklist.md`, `aif-artifact-audit.md` và template của repo GRM (`.specify/templates/`, `.claude/skills/sdlc-*`). Tài liệu này chốt cách các bước tiếp theo sẽ làm; chưa có code nào thay đổi.

## 0. Thứ tự trong một dự án mới

SRS và SDS là sản phẩm của các bước đầu dự án, không do từng run tạo. Run chỉ chạy khi dự án đã có hai tài liệu này.

| # | Bước | Ai làm | Sản phẩm | Cổng |
|---|---|---|---|---|
| 1 | Onboarding | AI soạn nháp, PM duyệt | Repo, file onboarding, `constitution.md` | G0 |
| 2 | Phân tích yêu cầu | Business Analyst (AI) soạn từ charter và ghi chú; người chốt qua workshop | `SRS.md` theo khung GRM: §1 giới thiệu và vai, §2 thực thể và business rules, §3 FR, §4 NFR, §5 UX, §6 luồng nghiệp vụ, §7 feature và US (chỉ story statement) | G1 · khách hàng ký SRS |
| 3 | Kiến trúc | Architect (AI) đề xuất, người chọn | `SDS.md` theo khung GRM: §2 traceability, §4/§13 kiến trúc, §4.3.3 DB schema, §6 API, §9 quyết định | G2 |
| 4 | Kế hoạch | Từ SRS §7 | Feature (`specs/NNN-slug/` + `sdlc.yaml`) và thứ tự US | PM và Tech Lead |
| 5 | Mỗi US một run | Theo AIF: Spec, Design, Quality, Implement | Run nối thêm vào SRS §7, SDS §5 và các file trong `specs/` | 4 cổng của run |

- Project check báo dự án **chưa sẵn sàng chạy run** khi thiếu `SRS.md` hoặc `SDS.md` đã duyệt.
- US của run được chọn từ SRS §7; Business Analyst viết AC trong `spec.md` từ FR ở SRS §3.
- Attendance là dự án thử đầu tiên: chạy bước 2 và 3 trước, rồi mới mở run cho từng US. Run 85d1a9fd tạo trước AIF, xem là run cũ.

## 1. Đơn vị công việc

| AIF-SDLC | AI-Native SDLC (đề xuất) |
|---|---|
| Một **User Story** (`PREFIX-US-NN`) đi qua 6 bước | Một **run** = một User Story |
| File theo **feature** trong `specs/NNN-slug/`, mỗi US nối thêm một section, không sửa section của US khác | Giữ nguyên: run nối section của US mình vào `spec.md`, `plan.md`, `tasks.md`, `test_cases.md`, `checklists/*.md` của feature |
| `sdlc.yaml` của feature (Epic, prefix, map US ↔ Jira Story, Test Env) | Giữ `sdlc.yaml` với `feature_id`, `slug`, `story_prefix`, `stories`; các trường Jira ghi `TBD` cho tới khi làm Jira |
| Mỗi bước 1 branch + 1 PR vào `develop`, cần 1 approval | **1 branch + 1 PR cho cả run**; mỗi bước là một commit riêng trên nhánh run, cổng duyệt nằm trên control room |
| Skill chạy trong IDE của từng người | Controller chạy các vai AI, người duyệt ở cổng |

Form New request chọn **Feature** và **User Story** có sẵn trong SRS §7 (hoặc thêm US mới vào một feature, khi đó run thêm story statement vào SRS §7). Controller cấp số US tiếp theo của feature (ví dụ `AT-US-03`).

## 2. Vai, artifact và cổng

| Bước AIF | Vai AI (hiện có) | Artifact theo template công ty | Cổng | Người duyệt |
|---|---|---|---|---|
| 1 Spec | Business Analyst | Section US trong `spec.md` (As a / I want to / so that, `AC-01…`, `EC-01…` Given/When/Then nhiều dòng); section US trong `checklists/requirements.md`; SRS §7 entry nếu repo có `SRS.md` | Spec (có sẵn) | Product Owner / BA |
| 2 Design | Tech Lead | Section US trong `plan.md` (Pre-flight G/A, Architecture: Design Review Findings, Package layout, Domain objects, Business rules, Sequence diagram, Error flows, Constitution notes, Element IDs, Open tasks); `checklists/security.md` khi US liên quan bảo mật; SDS §5 (Purpose) và các mục SDS bị ảnh hưởng nếu repo có `SDS.md` | Design (cổng plan hiện có) | Tech Lead / SE |
| 3 Quality | QA Analyst | Section US trong `test_cases.md`: AC Classification, Coverage Matrix (AC, Label API/UI/BOTH, Integration TC, E2E TC), `TC-NN` với US, Given, When, Then, AC, Type, Severity | **Quality (mới)**: tách khỏi cổng plan | QC (vai người mới) |
| 4 Implement | Controller + QA Engineer + Developer + Code Reviewer | `tasks.md` (render từ Open tasks và TC); báo cáo analyze trước và sau code; commit test (red check) rồi commit code; `review.md` | PR (có sẵn) | Code Owner |
| 5 Deploy | — | Không làm: platform không deploy | — | Người hoặc CI |
| 6 Verify | — | `test_report.md`: để giai đoạn E | — | — |

Vai AI vẫn trả về JSON đã kiểm schema như hiện nay; controller **render** JSON ra markdown đúng template. Nhờ vậy các luật định dạng (ID 2 chữ số, Given/When/Then nhiều dòng, `---` giữa các US…) luôn đúng mà không phụ thuộc model. Khi người sửa artifact ở cổng, controller parse lại và kiểm cùng các luật đó.

## 3. Nơi lưu file

| File | Nơi lưu | Ghi chú |
|---|---|---|
| `spec.md`, `plan.md`, `tasks.md`, `test_cases.md`, `checklists/requirements.md`, `checklists/security.md`, `sdlc.yaml` | `specs/NNN-slug/` trong repo dự án, trên nhánh run | Nằm trong PR như artifact của GRM |
| `SRS.md` §7, `SDS.md` §5 và các mục khác | Gốc repo | Chỉ khi repo đã có hai file này |
| `intent.md`, checklist đã tick, báo cáo analyze, `review.md`, `report.md` | `.sdlc/runs/<run-id>/` (như hiện nay) | Bằng chứng audit của run |
| Template | `.specify/templates/` của repo dự án; repo chưa có thì dùng bản mặc định của platform (bản generic lấy từ GRM) | Project check báo nguồn template đang dùng |
| Luật dự án | `.specify/memory/constitution.md` hoặc `constitution.md`, cùng `REVIEW.md` | Bước 5 |

Developer vẫn chỉ được sửa `paths.allowed`; các file trong `specs/` và SRS/SDS do controller ghi, không qua Developer.

## 4. Checklist ở mỗi cổng (bước 4)

Lấy từ `aif-review-checklist.md` §Step 1–4 và mục kiểm của template, chia ba loại:

| Loại | Ví dụ | Ai làm |
|---|---|---|
| Controller tự kiểm | AC/EC 2 chữ số, bắt đầu từ 01, không nhảy số; Given/When/Then mỗi vế một dòng; không có từ cấm (fast, user-friendly, appropriate, relevant, seamless, optimal, as needed, if possible); không còn `[ ]` trong checklist, mỗi `[~]` có lý do; mọi AC/EC có trong Coverage Matrix; BOTH/UI có E2E hoặc `e2e N/A: lý do`; TC đủ trường và AC tham chiếu tồn tại; TC đánh số liên tục; mỗi Open task có đường dẫn file; section của US cũ không bị đổi; không mật khẩu viết cứng; dữ liệu test có tiền tố `TestQC-` | Chặn cổng nếu trượt |
| AI tự kiểm | AC kiểm thử được và chỉ nói WHAT; thuật ngữ khớp constitution; truy vết về thực thể SRS §2 hoặc luồng §6; gap thuộc spec hay design; US có liên quan bảo mật không; phân lớp đúng; sơ đồ tuần tự đúng luật | Vai AI nộp kèm artifact, mỗi mục pass / fail / N/A kèm lý do |
| Người tick | AC đủ kịch bản và đủ rõ; TC thừa hoặc thiếu; quyết định hoãn `[~]`; chấp nhận rủi ro | Tick trên control room; lưu ai, khi nào, cho bản (hash) nào |

Mục về Jira, branch theo từng bước và Playwright của GRM không đưa vào platform. Mục riêng của stack (Spring, Flyway, Next.js…) nằm trong constitution của dự án.

## 5. Không lấy từ AIF

- 6 PR cho mỗi US, branch theo từng bước, merge vào `develop` giữa các bước.
- Jira (Task theo bước, Bug, comment): làm sau.
- Script PowerShell của spec-kit, đường dẫn Bitbucket, SSH deploy.
- Luật Java/Spring/Next.js ở mức platform.

## 6. Ảnh hưởng tới code (bước 2–7)

| Bước | Thay đổi chính |
|---|---|
| 2 Artifact | Contract `Spec` có user story `PREFIX-US-NN`, actor, AC/EC dạng Given/When/Then; renderer và parser theo template; thêm khái niệm feature (`specs/NNN-slug/`, `sdlc.yaml`); ghi file vào `specs/` thay vì chỉ `.sdlc/runs/`; run cũ vẫn đọc được theo định dạng cũ |
| 3 Quality | Trạng thái mới `DESIGNING_TESTS` và `WAITING_TEST_APPROVAL` giữa cổng plan và `READY`; QA Analyst chạy sau khi plan được duyệt; vai người QC; trang Quality trên control room |
| 4 Checklist | Bảng lưu checklist theo cổng; bộ kiểm cơ học; self-check của vai AI; UI tick |
| 5 Constitution | Project check đọc constitution; context của BA, Tech Lead, QA, Code Reviewer có constitution |
| 6 Prompt | Thêm mục Experience Sharing vào prompt `spec.md`, `plan.md`, `test-design.md`, `review.md`; chạy eval (hỏi trước khi dùng GX10) |
| 7 Analyze | Kiểm spec ↔ plan ↔ tasks ↔ TC trước khi code (cơ học + AI) và sau khi code; Critical/High chặn run |

## 7. Quyết định (2026-10-15)

| # | Câu hỏi | Chốt |
|---|---|---|
| 1 | Một run = một US; intent lớn thì BA đề xuất tách nhiều run | Có |
| 2 | Repo chưa có `SRS.md` / `SDS.md` | Không bỏ qua, không để run tạo khung: SRS và SDS làm ở bước Phân tích yêu cầu và Kiến trúc (mục 0) trước khi chạy run |
| 3 | Template mặc định của platform = 7 template GRM, bỏ phần Jira và Java | Có |
| 4 | Đánh số TC theo file thật của GRM: `TC-01`, tăng dần, tối thiểu 2 chữ số | Có |
| 5 | Vai người QC; tới giai đoạn F một operator giữ mọi vai, ghi "thay mặt QC" khi duyệt | Có |
