# Tiến độ và bàn giao

_Cập nhật: 2026-10-23_

## Điều phối nhiều story (23/10)

Thiết kế `poc-option-b/docs/multi-story-design.md` (người dùng chốt: bắt đầu khi phụ thuộc đã merge, merge `main` vào
nhánh, tự mở tối đa 2 run, Tech Lead duyệt backlog). **B1 xong**: bảng `backlogs` (migration 0011), `controller/app/backlog.py`,
prompt `backlog.md`, API `/projects/{key}/backlog` (propose, PUT, approve, `{story}/start`), trang Projects → Backlog.
Chưa gọi Propose order thật trên attendance (dùng hạn mức AI; để người dùng bấm). **Tiếp theo**: B2 (`delivery.auto_start`,
`max_open_runs`, `SDLC_WORKER_CONCURRENCY`, một job mỗi run, chặn chồng file ở cổng plan), rồi B3 (`update_branch`,
dựng lại tài liệu nền tảng, migration hai head, task Resolve conflicts).

Run List Employees `31d224b9`: ghi chú "đặt test vào `test.describe('EMP-US-02')`" bị gửi bằng Send defects, đối chiếu
SDS coi là trái test case → NEEDS_HUMAN. Đã thêm lựa chọn **Run the acceptance tests again instead**; người dùng bấm để
chạy lại Acceptance. Test: 318 passed.

## Foundation attendance (run a70e419f), 21–22/10

**Trạng thái (22/10):** plan v8 (T1–T4) đã pass và review; Acceptance chạy thật tới cổng QC (18/20 test pass sau khi
sửa locator). QC gửi AC-24 (thiếu field phải 400) → run được gỡ tay một lần sang mô hình mới: T1–T4 PASSED, T5 "Fix
the defects found by the QC" đã sửa `backend/app/api/main.py` (feat a492b9c); T5 dừng vì khiếu nại test AC-23 (405 qua
cổng web vs 401 ở API, brief khuyến nghị amend_tests) và validation exit 137 (thiếu bộ nhớ). Chờ người dùng quyết rồi
Review → Acceptance → cổng QC → PR. Sau đó người dùng muốn chạy lại Foundation (hoặc cả chuỗi) để kiểm nền tảng.

**Nền tảng đã sửa trong run này** (commit trên `feature/option-b-v2`; chi tiết DECISIONS.md, ARCHITECTURE.md,
`poc-option-b/docs/acceptance-design.md`, `foundation-design.md`, hướng dẫn sử dụng):
- Red check: red by construction (007d468); lint không tính đỏ, lỗi lint trong test về QA (dd46ac5); QA viết lại một
  lần khi bị từ chối, import nhiều dòng (8ce5f0c); Already built + bằng chứng (4d90a6d, 824d5f0).
- Khiếu nại: QA theo quyết định của Tech Lead, `UNCLEAR` (9b057d4); ruling theo plan (08af680); brief cho Tech Lead
  (a3f063a).
- Lockfile `environment.lock` và sandbox đọc của AI (be57302); prompt doing đọc lúc worker khởi động (9a5e246); task có
  job hỏng thành FAILED, resume chạy lại (bdf28a1).
- Preview/Acceptance: chỉ Start khi chờ người (18cc6c7); lỗi preview nêu service + log, phạm vi preview Foundation,
  plan đọc compose (7802178); Playwright được cài, bộ test cũ viết lại, kiểm môi trường chạy thử preview (3674dda);
  build timeout riêng (1013ed2); xoá file của bộ test cũ (bfde950); lỗi code test về QA một lần (24e1fce); Back to
  onboarding từ Acceptance quay lại Acceptance (889abb3); resume ở Acceptance (27ca98c).
- Gửi lỗi về: một task sửa lỗi Tn+1, task chờ phụ thuộc, job code bị huỷ khi run đã dừng (74bc922); QA Acceptance nhận
  ghi chú QC (e7b2b84).
- Backlog mới: 3.13 task song song, 3.14 test ngoài validation, 3.15 so biến môi trường, 3.16 brief cho lần dừng
  ngoài task, 3.17 deploy nền tảng lên Ubuntu, 3.18 tài nguyên Docker.

**Sau đó (22/10):** run Foundation attendance đã merge (người dùng). Đã làm UI/UX U1+U2 (7b6a25b, thiết kế `poc-option-b/docs/ux-concepts-design.md`): Brand and UX trong yêu cầu SDS, 3–5 concept UI có prototype do controller vẽ, cổng UI concept, `docs/ui/` vào PR của SDS, design system trong Foundation, mục UI ở cổng QC; U3 ở BACKLOG 3.19. Sửa thêm trong lúc chạy: task sửa lỗi sửa/tạo mọi file trong thư mục cho phép, ruling Keep giữ qua các lần chạy, file test do Developer tự viết bị bỏ, bộ test Acceptance viết lại khi tài khoản demo đổi, onboarding revision bắt đầu từ bản trước và Tech Lead tự sửa được file onboarding ở cổng, Back to onboarding lúc đang code quay lại code, Code Reviewer nhận quyết định của người và thông tin nền tảng, đối chiếu ghi chú lỗi với SDS. Tiếp theo: người dùng test các phần khác rồi reset attendance và chạy lại toàn bộ chuỗi.

**Bài học vận hành:** không chạy bộ test nền tảng (có test Docker) khi run đang code: gây exit 137 và test Docker
chập chờn (`test_a_project_environment_builds_its_image_and_reaches_its_services`).

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
| D6 Options + SDS (SA chọn phương án) | Chờ người dùng duyệt | 578a4f1 + sửa sau chạy thật |

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

### Chạy thật cả luồng (16/10 tối, dự án `discovery-trial`, file đạo văn 11 trang)

Repo thử cục bộ `poc-option-b/.data/demo-repos/discovery-trial` (merge "PR" bằng `git pull` từ worktree của run,
thay cho GitHub). Ở các cổng, Claude đóng vai người duyệt chỉ để đi tiếp, mọi chỗ đều gắn nhãn THỬ NGHIỆM:
mọi câu hỏi được hoãn (không bịa câu trả lời); quyết định D-002 đưa REQ-016, REQ-019 ra ngoài phạm vi; chọn PA1.

| Run | Bước | Kết quả |
|---|---|---|
| 160d89d8 | Discovery | 88 mục, 17 câu hỏi theo vai, 4 vòng (đọc PDF 1 lần; các vòng sau chỉ consolidate), baseline commit `requirements/` kèm PDF |
| f7ca490c | SRS từ baseline | SRS 57 KB, Appendix A truy vết đủ mọi REQ trong phạm vi; cổng SRS đạt |
| 9111958c | SDS + Options | 3 phương án (Cloud / On-premise / Hybrid) đối chiếu NFR; SDS §9.1 ghi PA1 và lý do; **chờ người dùng ở cổng SDS** |

Sửa sau chạy thật (đều chung cho mọi dự án): gắn function "mồ côi" vào feature (`links`); mục NFR không có số thành
cảnh báo của BA thay vì chặn; nút "Check the inventory again"; quyết định trực tiếp có tác động rõ (controller áp,
không qua AI); mục ngoài phạm vi trong truy vết SRS; `refs` chỉ nằm trong phần tử; questions.md/decisions.md chỉ
sinh phiên bản theo vòng.

## Trạng thái nền tảng

- `.env` (17/10): **mọi vai suy nghĩ trên `codex-readonly`** (`SDLC_TEAM_MODE`, `SDLC_DEFAULT_ENGINE`, `SDLC_ENGINES`,
  `SDLC_DISCOVERY_ENGINE`) vì hết hạn mức Claude; vai viết code vẫn Codex (`default_executor: codex`). Bản `.env` trước đó
  lưu ở scratchpad của phiên. Quay lại Claude: đặt ba khoá về `claude-readonly`, `./sdlc down && ./sdlc up`. Codex CLI cho vai viết code (model trong `~/.codex/config.toml`).
- Dữ liệu: dọn lần 3 ngày 19/10 theo yêu cầu. Database chỉ còn dự án `attendance` (0 run, readiness ready); mọi bảng
  run/eval đã truncate, worktree, log và source của run đã xoá. Repo attendance: `main` = commit bc5fe9e, chỉ còn file
  onboarding (`.sdlc/project.yaml`, AGENTS.md, CLAUDE.md, README.md, REVIEW.md), nhánh `sdlc/run-*` đã xoá, không
  force push. Kết quả trước khi dọn: Discovery 887374fa (PR #5), SRS 25d2a548 (PR #6), SDS be8fc101 (viết lại theo
  outline GRM: 4 lượt, 16 sơ đồ Mermaid, qua depth check, đang chờ cổng SDS thì bị xoá).
- Test lần cuối: 211 pass (bỏ test docker, test đó pass khi chạy riêng).

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

## Sửa ngày 19/10 (run Discovery 8a0b8f6e của attendance)

- Feature ra ngoài phạm vi kéo theo mục con (function, rule, permission…, theo `parent`, lồng nhiều cấp), trích cùng
  quyết định; mục mà một quyết định trong lượt đó nêu tên thì giữ theo quyết định đó (`_cascade_out_of_scope` trong
  `discovery/inventory.py`). Áp ở mỗi lượt consolidate (Extract again / Check the inventory again).
- Cổng baseline: function có feature cha đã ra ngoài phạm vi được báo rõ "belongs to REQ-064 …, which is out of scope
  (D-013)" thay vì "belongs to no feature". Cảnh báo NFR không số có trạng thái `warning` (vàng), không còn `fail`.
- Run 8a0b8f6e đang ở cổng baseline với inventory v3 cũ (REQ-065 còn trong phạm vi): Back to clarifying → Check the
  inventory again → Prepare baseline.
- Test: 226 pass (bỏ test docker).

## Sửa ngày 19/10: lý do từ chối SDS thắng phương án đã chọn

Run SDS 70de92e2: người dùng từ chối 2 lần vì không muốn Keycloak, SDS vẫn giữ. Lý do từ chối có tới cả 4 lượt, nhưng
prompt bảo stack của `chosen_option` (PA1 có Keycloak) "là của thiết kế" và giữ nguyên `current_sds` (43 chỗ Keycloak),
còn feedback không nói là được ưu tiên. Bốn prompt `sds-*.md` nay nói feedback ghi đè chosen_option và current_sds, bỏ
hay thay công nghệ ở mọi chỗ, không để lại làm phương án dự phòng; decision "Architecture Option" ghi thay đổi đó.

## Thêm ngày 19/10: xem góp ý đã được xử lý thế nào

- Spec, SRS, SDS có khoá `feedback_replies` [{feedback, change, where}] (`schemas.FeedbackReply`; SDS: mỗi lượt trả lời
  phần của mình, `write_sds` gộp lại). Prompt cấm nhắc góp ý/lượt/bản nháp trong chính tài liệu.
- Mỗi bản viết lại lưu `data.revision` = {from_version, feedback (lý do reject ở cổng spec hoặc request_changes trên PR
  kể từ bản trước), replies, sections (`documents.section_changes`)}; `GET /api/runs/{id}/documents/{kind}/diff?base=&head=`.
- Cổng spec/SRS/SDS: khung "Changes since vN" (`frontend/src/components/Revision.tsx`).
- Run SDS 70de92e2: đã điền `revision` cho v2, v3 bằng script (không có replies vì viết bằng code cũ). v3 đã bỏ Keycloak
  (10 chỗ còn lại đều là "thay Keycloak"), nhưng còn câu ghi chú nội bộ trong §1.3 và §9; chờ người dùng duyệt.
- Test: 227 pass.

## Thêm ngày 19/10: lần thử lại chạy tiếp từ bước đã xong

- Job tự thử lại tối đa `JOB_MAX_ATTEMPTS` (3) lần. Trước đây mỗi lần thử lại SDS viết lại từ lượt 1, Extract đọc lại
  từ đầu. Nay `joblog.resumable` lưu kết quả từng bước vào `<job>.checkpoints/` cạnh log; lần thử lại dùng lại bước có
  cùng đầu vào và cùng nội dung prompt (log ghi "reused the result of an earlier attempt"). Áp cho 4 lượt SDS, từng
  phần đọc nguồn của Extract và từng phần biên bản.
- Nguyên nhân job SDS 659f3b45 hỏng lần 1: Mac ngủ khi gập nắp, chạy pin (16:32–18:18). `caffeinate -i` không chặn
  được ngủ khi gập nắp; đồng hồ timeout 600s chỉ đếm lúc máy thức.
- Test: 228 pass. Dịch vụ CHƯA khởi động lại vì job SDS 659f3b45 đang chạy (lần 2); khởi động lại khi job xong.

## Thêm ngày 19/10: deployment view và phương án hosting

- Options: mỗi phương án có `hosting` (biến thể PA1-H1…: platform, services thành phần → dịch vụ, nfr_fit, cost,
  personal_data, operations, risks, pros/cons) và `recommended_hosting`; `architecture.hosting_platforms`,
  `max_hosting_variants` trong project.yaml (ghim vào effective_policy). Cổng Options kiểm có biến thể, đúng nền tảng,
  có khuyến nghị. SA chọn phương án + hosting (`hosting` trong POST options/choose); `chosen_option.hosting`.
- SDS: `zones`, `links`, `environments`, `resilience`; node có `zone`, `service`, `scaling`. §4.5 vẽ
  `mermaid_deployment` (subgraph lồng theo zone), bảng node/zone/kết nối/môi trường/backup. Depth check đòi sơ đồ §4.5;
  §9 phải ghi biến thể hosting đã chọn. Prompt options.md, sds-architecture.md, sds-operations.md cập nhật.
- Test: 229 pass. Chưa chạy thật với Codex.

## Thêm ngày 20/10: hỏi môi trường deploy trước khi so sánh hosting

- Run SDS 67de4971 ra AWS vì nguồn không nói gì về hạ tầng, Architect khuyến nghị AWS và giao diện chọn sẵn.
- `ArchitectureBrief.deployment_context` (`DeploymentContext`: platform, existing_infrastructure, data_residency,
  operations_team, budget, scale, availability), vào brief Markdown mục "Deployment context". Platform được kiểm với
  `architecture.hosting_platforms` khi tạo run; có platform thì `RunContext.hosting_platforms` = [platform].
- Options lưu `hosting_basis` (stated/assumed) cạnh dữ liệu (`stored_options` bỏ khoá này khi đọc). Assumed: cổng
  không chọn sẵn, cảnh báo, bắt buộc `hosting_reason` (vào `chosen_option` và §9). Options hiện "Questions for the
  Solution Architect". Prompt options.md, sds-architecture.md, sds-operations.md cập nhật.
- Run facec4d9: PA1 chỉ có AWS/Azure/GCP. Nay khi hosting chưa chốt, mỗi phương án phải có biến thể on-premise
  (`check_options(open_hosting=True)`), prompt yêu cầu phủ mô hình deploy (on-premise, một cloud, hybrid) trước hãng.
- Test: 230 pass. Chưa chạy thật với Codex.

## Thêm ngày 20/10: xem sơ đồ và ERD theo nhóm

- Sơ đồ trong control room: nút toàn màn hình (zoom bằng chuột, kéo, Fit, 100%), mở SVG ở tab mới, tải SVG
  (`frontend/src/components/DiagramViewer.tsx`).
- ERD cũ chỉ có bảng, không quan hệ, nên Mermaid xếp 24 bảng thành một hàng. Nay `foreign_keys` đọc "FK bảng.cột",
  "references bảng" hoặc tên `xxx_id`; ERD có quan hệ và PK/FK. `Table.group`: có từ 2 nhóm thì `data_schema` vẽ
  `mermaid_groups` (tổng quan nhóm, số FK giữa nhóm) rồi mỗi nhóm một ERD (bảng nhóm khác chỉ hiện tên). Prompt
  sds-architecture yêu cầu `group`, ghi "Primary key." và "FK bảng.cột". Thử trên dữ liệu thật run facec4d9 (nhóm gán
  tay): tổng quan 886×1062 thay vì 3548×990.
- Test: 231 pass.

## Thêm ngày 20/10: môi trường dự án và run Foundation

Thiết kế: `poc-option-b/docs/foundation-design.md`.
- Giai đoạn 1 (711aca3): `environment` trong project.yaml (`Environment`, `Service` trong `projects/config.py`);
  `ValidationGate.image()` build Dockerfile (tag `sdlc-validate-env-<project>:<sha12>`), `services()` mạng
  `--internal`, `ensure_setup` theo hash của `setup_inputs`; orphan cleanup xoá mạng. Test Docker thật với
  postgres:17-alpine pass.
- Giai đoạn 2: `RunKind.FOUNDATION`, trạng thái DESIGNING_ONBOARDING / WAITING_ONBOARDING_APPROVAL /
  PREPARING_ENVIRONMENT; `controller/app/foundation.py` (Onboarding, onboarding_files giữ phần khác của project.yaml,
  render diff, foundation_intent trích SDS); job `onboarding` (prompt `langflow/prompts/onboarding.md`, Architect trên
  engine của Tech Lead) và `environment`; cổng `onboarding` (Tech Lead), API `onboarding/approve|redo`; sau khi môi
  trường chạy được thì ghim lại artifacts rồi vào spec. Migration 0010 (approvals.stage 32). UI: tab Foundation, panel
  Onboarding, pipeline có Onboarding/Environment.
- Test: 237 pass. Chưa chạy thật với Codex trên attendance.

## Sự cố và thêm ngày 21/10

- Sự cố: ổ ảo Docker Desktop (58,4 GB) đầy → PostgreSQL của nền tảng sập lặp (recovery mode), API 500. Người dùng
  duyệt xoá build cache (10 GB) và image không tên; còn 9,5 GB trống, PostgreSQL tự khôi phục, dữ liệu đủ. Nên tăng
  Disk usage limit của Docker Desktop. Run Foundation 9ac65faa: lần build môi trường đầu (08:53) hỏng có lẽ cũng do ổ
  đầy, nhưng `--quiet` che lỗi nên run gửi lại Architect; job onboarding v2 đang chạy.
- Sửa: build dùng `--progress=plain`; "no space left on device" khi build/bật service → NEEDS_HUMAN, không gửi
  Architect. Test Docker xoá image nó build.
- Test double cho tích hợp: `Onboarding.test_doubles` (system, service, kind, fixtures, notes), `check_test_doubles`
  so với bảng SDS §13 (cảnh báo ở cổng onboarding, không chặn); prompt onboarding có bảng giao thức → bản giả; spec
  Foundation có kết quả "adapter + test double cho mọi hệ thống §13". BACKLOG 3.7: gọi ra ngoài qua allowlist.
- Test: 239 pass.

## Thêm ngày 21/10: preview và Acceptance

Thiết kế: `poc-option-b/docs/acceptance-design.md`.
- P1 (d30806d): `preview:` trong project.yaml (`Preview`, `Account`), `controller/app/preview.py`, job `preview`/
  `preview_stop`, API `POST /runs/{id}/preview`, `expire_previews` trong worker, khung Preview trên trang run;
  Onboarding viết `preview` và `.sdlc/preview.compose.yml`; spec Foundation đòi preview chạy được. Test compose thật
  (python:3.12-slim) pass: mở đúng 127.0.0.1.
- P2: trạng thái ACCEPTANCE_TESTING / WAITING_ACCEPTANCE_APPROVAL, `stages.acceptance`, `controller/app/acceptance.py`,
  `write_acceptance_tests` (QA Engineer, Playwright), cổng QC (accept/reject/retest), bằng chứng
  `.data/evidence/`, ghi chú của QC và của Code Owner (request_changes) tới Developer. Chưa chạy thật test Playwright
  (image Playwright chưa tải; ổ Docker còn ít chỗ).
- Test: 247 pass.

## Thêm ngày 21/10 (tối): sửa sau Foundation attendance, nhiều ngôn ngữ

- Grounding: task sửa file do task phụ thuộc tạo là hợp lệ; task xây code của một phần trước khi file dependency của phần
  đó (`setup_inputs`) có thì báo lỗi (T1 attendance có test backend, `backend/pyproject.toml` ở T2 → mọi lệnh python
  "not found"). Developer không đổi gì mà task đã có commit trước → cho qua, validation quyết định. Replan ghi lý do
  thành góp ý cho Tech Lead; commit của plan cũ không được dùng cho task cùng id (artifact `task_base`).
- Foundation có **Back to onboarding** ở cổng spec/plan/test; preview chờ code (`missing_parts`), hiện "unavailable".
- Nhiều ngôn ngữ: `validation[].image` và `validation[].setup` (BACKLOG 3.9–3.12 cho phần còn lại). Test Docker thật
  Python + Node trong hai container pass.
- Test: 257 pass.

## Bước tiếp theo

Attendance: SRS và SDS (PR #9) đã merge. Chạy **Foundation** trên attendance (Codex), duyệt Onboarding, rồi các story
theo thứ tự UPM → EMP/MSM → SHF/CAM → FEN → FRA/CIO → CAL → COR/DAS/RPT → NTF/HRI/AUD.

Người dùng chạy lại từ đầu trên `attendance`: Discovery → SRS từ baseline → SDS → story đầu tiên. Mọi vai đang chạy
`codex-readonly`.
