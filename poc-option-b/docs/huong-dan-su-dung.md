# Hướng dẫn sử dụng AI-Native SDLC

_Cập nhật 17/10/2026 · áp dụng cho branch `feature/option-b-v2`_

AI-Native SDLC là một đội phát triển phần mềm gồm các vai AI (Business Analyst, Architect, Tech Lead, QA, Developer, Code Reviewer) làm theo quy trình AIF-SDLC của công ty. Người duyệt ở mọi cổng: không bước nào tự merge hay deploy. Mọi thao tác làm trên **control room** tại http://127.0.0.1:8770; dòng lệnh chỉ dùng để bật, tắt và kiểm tra hệ thống.

## 1. Toàn cảnh

Một dự án đi qua bốn loại run, theo thứ tự:

```mermaid
flowchart LR
  raw["Tài liệu thô<br/><small>sales, product, biên bản</small>"] --> disc["1 Discovery<br/><small>bảng requirement, câu hỏi,<br/>quyết định, baseline</small>"]
  disc --> srs["2 SRS<br/><small>BA viết SRS.md<br/>từ baseline</small>"]
  srs --> sds["3 SDS<br/><small>Architect so sánh phương án,<br/>SA chọn, viết SDS.md</small>"]
  sds --> feat["4 Feature run<br/><small>mỗi user story trong SRS §7:<br/>spec, plan, test, code, PR</small>"]
```

| Run | Đầu vào | Kết quả trong repo | Cổng người duyệt |
|---|---|---|---|
| Discovery | File thô (PDF, DOCX, XLSX, PPTX, ảnh, văn bản dán) | `requirements/` (bảng requirement, câu hỏi, quyết định, baseline, file thô) | Baseline (PO), PR |
| SRS | Baseline đã merge, hoặc brief gõ tay | `SRS.md` | SRS (PO), PR |
| SDS | `SRS.md` đã merge + brief kiến trúc | `SDS.md` | Options (SA), SDS (Tech Lead), PR |
| Feature | Một user story của SRS §7 | `specs/NNN-slug/`, test, code | Spec (PO), Plan (TL), Test cases (QC), PR (Code Owner) |

Mỗi run kết thúc bằng một **draft PR**. Merge PR trên GitHub xong thì **không cần làm gì thêm**: trong vòng khoảng 1 phút, worker thấy PR đã merge, đóng run thành COMPLETED và **tự kiểm tra lại dự án**. Trang Projects và danh sách user story ở form Feature request nhờ đó được cập nhật. Khi tạo run mới, nếu nhánh mặc định vẫn đi trước lần kiểm tra cuối (ví dụ có người push thẳng lên `main`), platform kiểm tra lại thêm lần nữa. **Projects → Check again** chỉ cần khi anh sửa repo bằng tay và muốn xem kết quả ngay. Dự án nhỏ không có tài liệu thô thì bỏ qua Discovery và viết SRS từ brief.

## 2. Các vai

**Vai người.** Hiện một operator giữ mọi vai; mỗi quyết định ghi lại "thay mặt vai nào".

| Vai | Duyệt gì |
|---|---|
| Product Owner (PO) | Baseline của Discovery, SRS, spec của user story; trả lời câu hỏi về phạm vi |
| Solution Architect (SA) | Chọn phương án kiến trúc ở bước Options của SDS |
| Tech Lead | SDS, plan; xử lý run dừng ở `NEEDS_HUMAN` |
| QC | Test cases (cổng Quality) |
| Code Owner | Pull request |
| Platform Admin | Thêm dự án, kiểm tra onboarding |

Discovery có thêm vai trả lời câu hỏi theo cấu hình dự án: mặc định PO, BA, SA, TA (Technical Architect), QC, DPO (bảo vệ dữ liệu) và Sales (đầu mối khách hàng).

**Vai AI.**

| Vai | Làm gì | Engine mặc định |
|---|---|---|
| Business Analyst | Discovery: đọc nguồn, lập bảng requirement, đặt câu hỏi, đọc biên bản. SRS. Spec của user story | Claude (chỉ đọc). Discovery: theo `SDLC_DISCOVERY_ENGINE` (hiện là Codex) |
| Architect | So sánh phương án kiến trúc, viết SDS | Engine của Tech Lead |
| Tech Lead | Plan: task, file sửa, thứ tự | Claude (chỉ đọc) |
| QA Analyst | Test cases cho từng AC/EC | Claude (chỉ đọc) |
| QA Engineer, Developer | Viết test trước, rồi viết code | Codex |
| Validation Gate | Chạy test trong container không mạng | Docker |
| Code Reviewer | `review.md` (tham khảo) | Claude (chỉ đọc) |
| Release Engineer | Push branch, mở draft PR, theo dõi merge | `gh` của bạn |

## 3. Cài đặt và vận hành

**Cần có trên máy (một lần).**

| Cần | Kiểm tra |
|---|---|
| Docker Desktop đang chạy | `docker info` |
| uv | `uv --version` |
| GitHub CLI đã đăng nhập, có quyền push repo dự án | `gh auth status` |
| Claude Code đăng nhập bằng claude.ai | `claude auth status` |
| Codex CLI đăng nhập bằng ChatGPT | `codex login status` |
| `pdftotext` (để Codex đọc PDF) | `pdftotext -v` (cài: `brew install poppler`) |

**Lệnh.** Chạy trong `poc-option-b/`:

| Lệnh | Việc |
|---|---|
| `./sdlc init` | Tạo `.env` lần đầu |
| `./sdlc up` | Bật database, API, worker; in địa chỉ control room |
| `./sdlc down` | Tắt mọi thứ; dữ liệu vẫn giữ |
| `./sdlc doctor` | Kiểm tra từng thành phần, CLI đã đăng nhập chưa |
| `./sdlc logs api` / `worker` | Xem log |

**Cấu hình chính trong `.env`.** Danh sách đầy đủ và giá trị mặc định ở `.env.example`.

| Khoá | Giá trị đang dùng | Ý nghĩa |
|---|---|---|
| `SDLC_TEAM_MODE` | `codex-readonly` (từ 17/10, hết hạn mức Claude) | Engine cơ bản của các vai suy nghĩ, cả nút Draft with AI |
| `SDLC_ENGINES` | mọi vai `codex-readonly` | Engine từng vai: `business_analyst`, `tech_lead`, `qa_analyst`, `code_reviewer` |
| `SDLC_DISCOVERY_ENGINE` | `codex-readonly` | BA của run Discovery, khi khác `SDLC_ENGINES`; để trống thì dùng như các run khác |
| `SDLC_DOING_MODE` | `cli` | Codex/Claude viết test và code thật (`mock` để thử không gọi AI) |
| `SDLC_QWEN_ENABLED` | `false` | Qwen trên GX10 đã tắt |
| `SDLC_KEEP_AWAKE` | `true` | Giữ Mac không ngủ khi có job (macOS) |

Lưu ý khi vận hành:
- Sửa `.env` thì chạy `./sdlc down && ./sdlc up`. Engine được ghim vào mỗi run lúc tạo, nên run đang mở giữ engine cũ.
- **Không khởi động lại khi có job đang chạy** (run ở `EXTRACTING`, `SPECIFYING`, `DESIGNING_OPTIONS`, `CODING`…): job đó mất và phải chạy lại. Xem tab **Runs** trước.
- Máy ngủ thì job dừng theo. Worker tự giữ máy thức khi có job, nhưng gập máy vẫn làm máy ngủ.

**Chi phí.** AI chạy qua subscription Claude và ChatGPT bằng CLI, không dùng API key, nên **không phát sinh tiền**. Con số "$" trong log và trang Reports chỉ là ước tính theo giá API. Thứ bị tiêu là **hạn mức** của gói (theo khung 5 giờ và theo tuần). Hết hạn mức thì job tự hoãn 15 phút rồi chạy lại.

## 4. Thêm dự án (Platform Admin)

Repo của dự án cần bốn file ở gốc, commit lên nhánh mặc định:

| File | Nội dung |
|---|---|
| `AGENTS.md` | Kiến thức cho mọi vai AI: lệnh test, cấu trúc, quy ước, những gì không được đụng |
| `CLAUDE.md` | Một dòng `@AGENTS.md` |
| `REVIEW.md` (tuỳ chọn) | Thế nào là lỗi quan trọng, thế nào là góp ý nhỏ, cho Code Reviewer |
| `.sdlc/project.yaml` | Lệnh validation, đường dẫn được sửa, đường dẫn test, giới hạn, chế độ từng stage, cấu hình Discovery |

Ví dụ `.sdlc/project.yaml` rút gọn:

```yaml
schema_version: 1
project: attendance              # phải trùng key khi thêm dự án
default_branch: main
validation_image: node-20        # node-20 | node-22 | python-3.12 | python-3.13
validation:
  - name: unit-tests
    run: ["node", "--test"]
paths:
  allowed: ["src/**", "package.json"]   # Developer chỉ sửa ở đây
  tests: ["test/**"]                    # QA Engineer chỉ thêm test ở đây
  protected: ["AGENTS.md", "CLAUDE.md", "REVIEW.md"]
default_executor: codex
limits:
  max_tasks: 5
  thinking_timeout_minutes: 20   # một lần gọi vai suy nghĩ; tài liệu dày cần lâu hơn
stages:
  spec: gated
  plan: gated
  options: gated                 # bước Options của SDS; "skip" để bỏ
discovery:                       # bỏ trống thì dùng mặc định
  commit_sources: true           # file thô vào requirements/sources/
  limits:
    max_rounds: 10
architecture:                    # bước Options của SDS; bỏ trống thì dùng mặc định
  hosting_platforms: [aws, azure, gcp, on_premise, hybrid]   # khách chỉ nhận on-premise thì để [on_premise]
  max_hosting_variants: 3
```

Vào **Projects → Add a project**: nhập **Key** (bằng `project:`) và URL repo GitHub. Nền tảng clone một bản mirror, kiểm tra file onboarding, chạy từng lệnh validation trong container, và kiểm quyền push. Dự án phải ở trạng thái **ready**. Có dòng báo lỗi thì sửa trong repo, push rồi bấm **Check again**.

## 5. Discovery: từ tài liệu thô tới baseline

### 5.1 Tạo discovery và thêm nguồn

1. **New request → tab Discovery**: chọn dự án, nhập tên (và ghi chú nếu có), bấm **Start discovery, then add sources**. Mỗi dự án chỉ có một discovery mở tại một thời điểm.
2. Trang run tự mở tab **Sources** (hoặc bấm **Add files** ở panel phía trên):
   - Kéo thả file vào khung, hoặc bấm vào khung để chọn. Nhận PDF, DOCX, XLSX, PPTX, PNG/JPG/WEBP, MD, TXT, CSV, EML; tối đa 50 MB và 300 trang mỗi file.
   - **Paste text** để dán email, chat, ghi chú.
   - Tick **These are minutes of a meeting** trước khi thêm nếu đó là biên bản họp: BA sẽ rút *quyết định* thay vì requirement.
   - Mỗi file lưu kèm SHA-256. **Remove** cần lý do; file vẫn giữ lại để kiểm tra sau.
3. Bấm **Start extraction**. BA đọc mỗi lượt 10 trang PDF (hoặc 30.000 ký tự văn bản), sau đó một lượt **consolidate** gộp mục trùng, gắn function vào feature và đặt câu hỏi cần đọc cả bộ mới thấy. Tiến độ xem ở tab **Jobs, logs & decisions**.

Thời gian tham khảo: khoảng 5–7 phút cho 10 trang trên Claude, khoảng 1,5 phút cho 2 trang trên Codex, cộng 1–4 phút cho lượt consolidate.

### 5.2 Đọc kết quả

| Tab | Có gì |
|---|---|
| **Requirements** | Bảng requirement gom theo loại (Goals, Actors, Permissions, Features, Functions, Rules, Data, Integrations, NFR…). Mỗi mục có REQ id, nội dung, thuộc feature hay actor nào, giai đoạn, và nguồn dạng `p. 7`: bấm vào là mở đúng trang PDF. Lọc theo loại, tìm kiếm, chọn phiên bản |
| **Questions** | Câu hỏi Q-001… gom theo vai trả lời; chặn (blocking) hay không |
| **Decisions** | Quyết định D-001…: từ câu trả lời, từ biên bản, hoặc ghi trực tiếp |
| **Thread** | `inventory.md`, `questions.md`, `decisions.md`, `baseline.md` theo phiên bản; thu gọn từng khối được |

Bảng requirement dùng một schema chung cho mọi dự án (15 loại mục). BA chỉ lấy requirement của **sản phẩm**; phần tài liệu nói về chính nó (mục đích bản tài liệu, người soạn, mẫu) không thành mục. Thiếu thông tin thì BA đặt câu hỏi, không tự điền.

### 5.3 Làm rõ: câu hỏi và quyết định

Trên tab **Questions**, với mỗi câu:
- **Suggested answer**: câu trả lời BA gợi ý kèm căn cứ (slide, trang, hoặc "thông lệ"). Bấm **Use as answer** để điền vào ô trả lời, sửa nếu cần rồi bấm **Answer**. BA không có căn cứ thì không gợi ý; gợi ý chỉ lặp lại câu hỏi hay ra lệnh ("Xác định…", "Cung cấp…") bị controller bỏ đi. Câu hỏi mở chưa có gợi ý sẽ được gợi ý ở lượt **Check the inventory again** hoặc **Extract again** sau.
- **Answer**: gõ câu trả lời. Nếu là ý của khách hàng thì ghi rõ "từ khách hàng, qua Sales". Câu trả lời tự thành một quyết định.
- **Defer**: hoãn sang sau, kèm lý do. Câu hoãn không chặn baseline và được ghi vào baseline.
- **Withdraw**: rút câu hỏi không còn đúng, kèm lý do.
- **Move to…**: chuyển cho vai khác.
- **Workshop list**: tải danh sách câu hỏi đang mở của một vai (hoặc tất cả), để mang vào buổi họp.

Inbox có thẻ **Questions for you** cho từng vai còn câu hỏi mở.

BA chia câu hỏi làm hai mức:
- **Baseline:** cần chốt mới xác định được phạm vi, actor và quyền, luồng chính, luật nghiệp vụ khách hàng quyết, tích hợp, pháp lý. Những câu này hiện trong tab Questions để trả lời.
- **Later:** chi tiết để SRS, SDS hoặc spec của từng story quyết, như thời gian chính xác, danh sách trường, hành vi màn hình, thông số kỹ thuật. Controller **tự hoãn** các câu này với lý do "for the SRS, SDS or a story's spec to settle"; chúng vào baseline ở mục câu hỏi hoãn, và run SRS nhận lại. Muốn xem thì tick **show answered, deferred, withdrawn**; muốn trả lời ngay thì bấm **Open again**.

Sau vòng đầu, BA chỉ hỏi thêm về những gì *mới*: nguồn hay biên bản mới, hoặc một quyết định mâu thuẫn với mục hay quyết định khác. BA không hỏi sâu thêm vào chi tiết của một câu đã trả lời.

Trên tab **Decisions**:
- **Record a decision**: ghi quyết định trực tiếp, kèm REQ id. Chọn **Effect** để controller áp đúng như ghi: *Confirm the items*, *Put the items out of scope*, hoặc *Change the item text* (kèm nội dung mới). Không chọn effect thì BA tự đọc quyết định ở vòng sau.
- Quyết định BA rút từ biên bản ở trạng thái **proposed**: vai sở hữu bấm **Accept** hoặc **Reject**. Accept thì câu hỏi liên quan được đánh dấu đã trả lời.

### 5.4 Extract again và giới hạn số vòng

- **Extract again**: chỉ đọc nguồn và biên bản **mới**, rồi áp các quyết định đã duyệt. REQ id không đổi giữa các vòng. Không có gì mới thì nút bị từ chối, để khỏi tốn hạn mức.
- **Check the inventory again**: chỉ chạy lượt consolidate, không đọc nguồn, khi muốn BA kiểm lại toàn bộ bảng (gắn function vào feature, gộp mục trùng, gợi ý câu trả lời). Lượt này chỉ đặt câu hỏi khi thấy mâu thuẫn, và **không tính vào số vòng**.
- Mặc định tối đa **10 vòng** có nội dung mới (`discovery.limits.max_rounds`). Hết số vòng thì panel hiện ô **Why another round**: PO ghi lý do rồi bấm **Confirm another round**. Mỗi vòng vượt giới hạn được ghi thành event.

### 5.5 Chốt baseline

1. Mở **Prepare the baseline (Product Owner)** trong panel, ghi **Scope of this phase** (phạm vi đã thoả thuận với khách hàng), bấm **Prepare baseline**.
2. Ở cổng baseline có checklist:
   - **Controller tự kiểm, trượt thì không duyệt được:** không còn câu hỏi chặn hay câu mâu thuẫn đang mở; không còn quyết định chờ duyệt; bảng đã áp mọi quyết định (nếu chưa thì Extract again); mọi mục có nguồn hoặc quyết định; mỗi feature trong phạm vi có ít nhất một function, và mỗi function thuộc một feature. Đưa một feature (hay actor) ra ngoài phạm vi thì các mục con của nó (function, rule, permission…) ra theo, trích cùng quyết định; inventory cũ chưa có điều này thì cổng ghi rõ "thuộc REQ-xxx đã ra ngoài phạm vi (D-xxx)", bấm Back to clarifying rồi Check the inventory again.
   - **Cảnh báo của BA** (nhãn `warning` màu vàng): NFR chưa có con số. Mục này không chặn, vì NFR định tính như "mã hoá dữ liệu" vốn không có số.
   - **PO tick:** phạm vi đúng với thoả thuận; các mục ngoài phạm vi và câu hoãn chấp nhận được; danh sách actor và quyền đã đủ.
3. **Approve baseline**: platform commit `requirements/` lên branch của run (`baseline.md`, `baseline.json`, `inventory.md`, `questions.md`, `decisions.md`, `sources.md`, `sources/`) rồi mở draft PR. **Back to clarifying** để quay lại sửa.
4. Merge PR trên GitHub; worker ghi nhận trong vòng 1 phút và đóng run Discovery. Sau đó vào **Projects → Check again**.

## 6. SRS

1. **New request → Project SRS**. Có baseline thì tick **Build on the discovery baseline**: chỉ cần tên sản phẩm, ghi chú tuỳ chọn. Không có baseline thì điền brief: tóm tắt, users và roles, feature mong muốn, ràng buộc.
2. BA viết `SRS.md` theo khung công ty (§1–§8). Khi dựng từ baseline, mỗi FR, user story, rule, role… ghi lại REQ mà nó lấy từ đó, và SRS có thêm **Appendix A. Requirement traceability**.
3. Ở cổng SRS, controller kiểm: đủ các mục §1–§8, story id không trùng, và mọi REQ trong phạm vi đều được dùng; mục `option` để dành cho SDS. PO tick 3 mục rồi **Accept** (hoặc **Edit**, **Reject**). Run mở draft PR; merge rồi **Check again**.

Nếu baseline chưa merge, run dừng ở `NEEDS_HUMAN` với lý do "merge the discovery pull request first".

### Khi SRS (hoặc SDS) chưa đạt

| Tình huống | Làm gì |
|---|---|
| Sai ít, anh tự sửa được | **Edit** ở cổng, sửa Markdown, **Save as new version**. Controller kiểm lại khung và truy vết; bản sửa ghi là của người |
| Cần AI viết lại theo góp ý | Ghi rõ cần đổi gì vào ô **Reason**, bấm **Reject: rewrite**. BA (hoặc Architect) nhận **bản nháp mới nhất cùng góp ý**, sửa đúng chỗ, giữ nguyên ID, rồi cổng duyệt lại |
| Đã qua cổng, mở PR rồi mới thấy sai | Ở panel PR, ghi lý do rồi bấm **Request changes**. Run quay về bước viết tài liệu như trên; duyệt xong thì **cùng PR đó** được cập nhật |
| Sai từ gốc requirement (thiếu, sai phạm vi) | Sửa ở Discovery: bổ sung nguồn hoặc quyết định trong một discovery mới, chốt baseline mới, merge, rồi chạy lại SRS từ baseline đó |
| Không cần nữa | **Reject: cancel run**, hoặc **Close without merge** ở bước PR |

Ở bước PR của **Discovery**, **Request changes** đưa run về Clarifying; lần chốt baseline sau sẽ cập nhật cùng PR đó.

## 7. SDS và bước Options

1. **New request → Project SDS** (cần `SRS.md` đã merge). Ô **Let the Architect propose the stack** được tick sẵn: chỉ cần tên sản phẩm. Ô Stack, Deployment, Integrations thành tuỳ chọn; điều gì anh điền vào đó là **ràng buộc** mà mọi phương án phải theo (ví dụ "bắt buộc PostgreSQL", "chạy trong mạng nội bộ"). Bỏ tick thì phải tự điền stack như cách cũ.
   - Phần **Deployment context** (nên điền): **Hosting platform** (AWS / Azure / Google Cloud / On-premise / Hybrid, hoặc *Not decided yet*), hạ tầng sẵn có, nơi dữ liệu phải lưu, đội vận hành, ngân sách, quy mô (site, người dùng, thiết bị), yêu cầu uptime/phục hồi. Chọn nền tảng thì mọi biến thể hosting đều chạy trên nền tảng đó và §4.5 vẽ đúng môi trường của khách. Nền tảng phải nằm trong `architecture.hosting_platforms` của dự án.
2. **Options** (bật mặc định): Architect so sánh 2–4 phương án kiến trúc, gồm mọi phương án có trong baseline cộng phương án tự đề xuất. Mỗi phương án có **stack theo từng lớp** (frontend, backend, database, AI, hạ tầng, giám sát…) kèm lý do, **deployment** và **tích hợp**. Tất cả suy ra từ SRS, baseline và stack hiện có của repo (`AGENTS.md`). Mỗi phương án được đánh giá theo NFR, chi phí, rủi ro, nơi lưu dữ liệu nhạy cảm, vận hành, kèm phương án Architect khuyến nghị. Mỗi phương án còn có **2–3 biến thể hosting** trên các nền tảng của `architecture.hosting_platforms` (AWS, Azure, GCP, on-premise, hybrid): cùng các thành phần, khác nơi chạy. Mỗi biến thể có bảng **thành phần → dịch vụ** (ví dụ API → Amazon ECS on Fargate, hoặc VM với Docker), chi phí, nơi lưu dữ liệu cá nhân/sinh trắc học, vận hành, rủi ro, và biến thể Architect khuyến nghị. Nền tảng mà yêu cầu loại trừ (dữ liệu phải ở trong nước, không dùng cloud…) không có biến thể.
3. Cổng Options (vai SA): đọc bảng so sánh, bảng stack và bảng hosting của từng phương án, rồi chọn phương án và **Hosting of PAx**. Brief đã nêu môi trường deploy thì biến thể khuyến nghị được chọn sẵn. Brief **không** nêu thì mỗi phương án phải so sánh các **mô hình deploy** khác nhau, không phải nhiều hãng cloud: luôn có **on-premise** (controller kiểm), một cloud phù hợp nhất, và hybrid khi phương án tách giữa site và trung tâm; khuyến nghị chỉ là giả định của Architect: không chọn sẵn, có cảnh báo vàng, kèm **Questions for the Solution Architect** (điều gì quyết định hosting), và SA phải ghi **Why this hosting** (vào SDS §9). Controller kiểm: mỗi phương án có biến thể hosting, nằm trong các nền tảng của dự án, có một biến thể được khuyến nghị.
   - Ghi **Adjustments to the chosen option** nếu muốn bổ sung hoặc sửa, ví dụ "dùng PostgreSQL thay MySQL; thêm Redis cho hàng đợi". Architect phải áp đúng các điều chỉnh này.
   - Ghi **Why this option** (bắt buộc), tick checklist rồi bấm **Choose PAx**.
   - Muốn Architect so sánh lại với góp ý thì ghi lý do rồi bấm **Ask for other options**, ví dụ "thêm phương án on-premise cho PA1".

#### Thêm một nền tảng hosting khác (Alibaba Cloud, Viettel Cloud, FPT Cloud…)

Architect chỉ được đề xuất biến thể trên các nền tảng trong `architecture.hosting_platforms`. Ghi góp ý "thêm Alibaba Cloud" ở cổng Options **chưa đủ**: Architect sẽ đưa biến thể, nhưng controller báo ✗ *"hosting outside architecture.hosting_platforms"* và không cho duyệt. Cách làm:

1. Thêm nền tảng vào `.sdlc/project.yaml` của repo dự án, merge vào nhánh mặc định:
   ```yaml
   architecture:
     hosting_platforms: [aws, azure, gcp, alibaba_cloud, on_premise, hybrid]
   ```
   Tên viết thường, nối bằng `_` (`alibaba_cloud`, `viettel_cloud`, `fpt_cloud`, `oracle_cloud`…). Giao diện tự hiển thị thành "Alibaba Cloud"; Architect viết "Alibaba Cloud" hay "alibaba-cloud" đều được quy về `alibaba_cloud`. Khách chỉ nhận một nền tảng thì để danh sách một phần tử, ví dụ `[on_premise]`.
2. **Projects → Check again** để nền tảng đọc lại cấu hình.
3. Tạo **run SDS mới**. Danh sách nền tảng được ghim vào run lúc bắt đầu, nên run đang mở vẫn dùng danh sách cũ; ở run đó chỉ xin thêm biến thể trên nền tảng đã có trong danh sách.

Lưu ý: ô **Hosting platform** trong form SDS hiện chỉ liệt kê AWS, Azure, Google Cloud, On-premise, Hybrid. Muốn khách dùng nền tảng mới (ví dụ chỉ Alibaba Cloud), để ô này *Not decided yet* và ghi vào ô **Existing infrastructure** hoặc **Deployment and infrastructure**, ví dụ "khách đã có hợp đồng Alibaba Cloud, bắt buộc dùng"; hoặc để `hosting_platforms: [alibaba_cloud]` trong `project.yaml`.
4. Architect viết `SDS.md` (§1–§13, đủ outline của template công ty như SDS của GRM) **qua 4 lượt**: mô hình miền và UI (§1–3); kiến trúc (§4); API, bảo mật, NFR (§6–8); quyết định, metric, phụ lục, tích hợp (§5, §9–13). Lượt sau đọc những gì lượt trước đã viết. Controller tự vẽ sơ đồ Mermaid từ dữ liệu (context, logical view, **deployment**, quan hệ, state, ERD, sequence cho từng luồng, luồng đăng nhập). **§4.5 Physical View** là deployment view theo biến thể hosting đã chọn: sơ đồ các vùng (tài khoản cloud, VPC/subnet, data centre, LAN của site, Internet) với node bên trong và kết nối có giao thức/port và cách bảo mật; bảng node (vùng, dịch vụ host, chạy gì, scale); bảng kết nối; bảng môi trường (dev/test/staging/prod); backup, phục hồi (RPO/RTO), giám sát; control room và GitHub đều hiển thị được. **§4.3.4 Data Design** vẽ quan hệ từ khoá ngoại (cột ghi "FK bảng.cột" hoặc tên dạng `xxx_id`), đánh dấu PK/FK; khi Architect chia bảng theo nhóm nghiệp vụ (`group`), có một sơ đồ tổng quan các nhóm (mỗi nhóm liệt kê bảng, mũi tên ghi số khoá ngoại giữa hai nhóm) rồi mỗi nhóm một ERD chi tiết xếp từ trên xuống, bảng của nhóm khác chỉ hiện tên. Sơ đồ lớn (ERD, deployment) trong control room: bấm vào sơ đồ hoặc nút ⤢ để mở **toàn màn hình** (lăn chuột để zoom tại con trỏ, kéo để di chuyển, nút **Fit** / **100%**, phím `+` `-` `0`, Esc để đóng); nút ↗ mở file SVG ở tab mới để zoom bằng trình duyệt hoặc in; nút ⬇ tải file `.svg` để gửi khách hoặc dán vào slide. Architect viết theo stack của phương án đã chọn cộng các điều chỉnh; mục §9.1 "Architecture Option" ghi phương án đã chọn, lý do của SA, các điều chỉnh và các phương án đã cân nhắc. Ở cổng SDS (Tech Lead), controller kiểm thêm: dòng Decision của §9 có ghi phương án và biến thể hosting đã chọn, và **độ sâu**: có sơ đồ context, sơ đồ deployment ở §4.5, sơ đồ dữ liệu, sequence cho mọi luồng SRS §6, state diagram cho mọi thực thể SRS §2.4, DTO, API specification và luồng đăng nhập. Thiếu thì không duyệt được; dùng **Reject: rewrite** kèm góp ý. Duyệt, merge PR, **Check again**.

Muốn bỏ bước Options thì đặt `stages.options: skip` trong `.sdlc/project.yaml`.

## 7a. Foundation: file onboarding theo SDS và khung dự án

Sau khi `SDS.md` merge và **trước story đầu tiên**. Mọi thứ đi qua nền tảng, không sửa tay repo.

1. **New request → Foundation** (cần `SDS.md` trên nhánh mặc định): nhập tên sản phẩm và ghi chú (ví dụ "chưa cần CI").
2. **Onboarding** (Architect viết, cổng **Tech Lead**): từ SDS (stack, cấu trúc §4.3.5, physical view, nguyên tắc, API, bảo mật, quy ước), Architect viết lại `AGENTS.md`, `REVIEW.md`, `.gitignore`, workflow CI, phần kỹ thuật của `.sdlc/project.yaml` (paths, validation, setup, `environment`, `limits.max_tasks/max_changed_files`) và `.sdlc/validation.Dockerfile` khi không toolchain nào của nền tảng đủ cho cả stack. Các phần khác của project.yaml (project, executors, stages, discovery, architecture, các limit khác) giữ nguyên. Cổng hiện **diff từng file** với nhánh mặc định; controller kiểm project.yaml mới đọc được (không thì tự gửi lại Architect kèm lỗi). **Approve onboarding** hoặc **Ask for changes** (khung "Changes since vN" như ở SRS/SDS).
3. **Environment**: controller commit các file lên nhánh của run (vai AI viết code vẫn không được sửa chúng), build image, bật service. Lỗi thì quay lại Architect kèm log, tối đa `limits.max_remediations` lần, rồi dừng chờ người.
4. Môi trường chạy được: run **ghim lại** policy, `AGENTS.md`, `REVIEW.md` từ nhánh của run, rồi như một story: **spec** (khung dự án: cấu trúc, cấu hình, migration nền, health check, đăng nhập và phân quyền nền của SDS §7, không làm chức năng của story nào) → plan → test cases → code → review → PR.
   Ở cổng Spec, Plan hay Test cases mà phát hiện file onboarding còn thiếu (ví dụ một thư mục plan cần nhưng chưa nằm trong `paths.allowed`): ghi lý do, bấm **Back to onboarding**. Architect sửa, anh duyệt lại, Environment kiểm lại, rồi run quay về đúng bước đó; spec đã duyệt không bị viết lại.
5. Merge PR, **Projects → Check again**: validation mới chạy trên nhánh mặc định; từ đây các run story dùng stack và môi trường mới.

### Môi trường validation của dự án (`environment`)

Dự án mà một toolchain của nền tảng (`node-20/22`, `python-3.12/3.13`) không đủ thì khai báo trong `.sdlc/project.yaml` (bước Onboarding tự viết phần này):

```yaml
environment:
  dockerfile: .sdlc/validation.Dockerfile   # nền tảng build (có mạng), một lần cho mỗi nội dung file
  setup_inputs: [backend/requirements*.txt, web/package-lock.json]   # đổi thì chạy lại setup
  env: {DATABASE_URL: "postgresql://app:app@postgres:5432/app"}
  services:
    - name: postgres                       # tên máy trong mạng của lần validation
      image: postgres:17-alpine
      env: {POSTGRES_USER: app, POSTGRES_PASSWORD: app, POSTGRES_DB: app}
      ready: [pg_isready, -U, app, -h, 127.0.0.1]
```

**Lệnh lint** (`validation[].kind`): `lint` cho linter/formatter (ruff, eslint), `check` cho test và typecheck; mặc định `auto` (tên có chữ `lint` thì là lint). Ở red check, lệnh lint fail không tính là "test đỏ"; nếu lỗi lint nằm trong file test QA vừa viết thì trả về QA sửa ngay, trước khi Developer code.

**Lockfile** (`environment.lock`): Developer làm việc không có mạng nên không tự sinh được lockfile đúng. Khai báo một lệnh (argv) sinh lockfile từ manifest, ví dụ `[sh, -ec, "npm --prefix web install --package-lock-only --ignore-scripts && uv lock --directory backend"]`. Khi một task đổi file trong `setup_inputs`, trước validation nền tảng chạy lệnh này trong image validation **có mạng**, rồi commit `build(Tn): lock dependencies`. Lệnh chỉ được đổi file nằm trong `setup_inputs`; lệnh lỗi thì lỗi về Developer như một lần validation hỏng.

**Phạm vi đọc của AI**: lệnh mà Codex chạy (Developer, QA, các vai suy nghĩ) chỉ đọc được worktree của run, thư mục cài các công cụ trên PATH, repo git của worktree và `SDLC_EXECUTOR_READ_PATHS` (mặc định `/Library/Developer/CommandLineTools,/System/Library/OpenSSL`); dự án khác, `~/.ssh`, `.env` của nền tảng thì không. Phiên Claude bị hook chặn Read/Glob/Grep ngoài worktree. Không có mạng. Công cụ cài ở chỗ khác mà lệnh cần thì thêm thư mục đó vào `SDLC_EXECUTOR_READ_PATHS`.

**Nhiều ngôn ngữ** (ví dụ Java hoặc .NET cho backend và React cho frontend): thay vì gộp mọi toolchain vào một image, mỗi lệnh validation có thể ghi `image` riêng (tên toolchain của nền tảng như `python-3.13`, `node-22`, hoặc image chính thức như `eclipse-temurin:21-jdk`, `golang:1.23`, `mcr.microsoft.com/dotnet/sdk:8.0`) và `setup` riêng (cài dependency trong đúng image đó, có mạng; các lệnh cùng image và setup chỉ cài một lần, cài lại khi `setup_inputs` đổi). Khi mọi lệnh đều có `image` thì không cần `validation_image` hay `dockerfile`:

```yaml
validation:
  - name: backend-tests
    image: eclipse-temurin:21-jdk
    setup: [./mvnw, -q, dependency:go-offline]
    run: [./mvnw, -q, test]
  - name: web-tests
    image: node-22
    setup: [npm, --prefix, web, ci]
    run: [npm, --prefix, web, test]
```

Mỗi lần validation: mạng Docker riêng **không ra Internet**, bật service, chờ lệnh `ready`, chạy lệnh validation với `env`, xong thì xoá service và mạng. Setup (cài dependency, có mạng) chạy lại khi file trong `setup_inputs` đổi. Chỉ dùng mật khẩu test.

### Hệ thống bên ngoài: dùng bản giả (test double)

Test **không bao giờ** gọi hệ thống thật. Mỗi tích hợp trong SDS §13 có một bản giả chạy như một service trong mạng của lần validation; code gọi hệ thống ngoài qua adapter (tầng `integrations` của SDS) và địa chỉ lấy từ biến môi trường, nên chỉ đổi URL là chuyển giữa bản giả và bản thật. Bước Onboarding tự chọn bản giả theo giao thức SDS ghi và liệt kê trong bảng **External systems and their test doubles**:

| Loại tích hợp | Bản giả |
|---|---|
| REST/SOAP API (HR, payroll, CRM, đối tác, SMS, push, thanh toán) | WireMock (mapping JSON), Prism (từ OpenAPI) |
| OAuth2 / OIDC / SSO | Keycloak với realm test, mock-oauth2-server |
| Lưu trữ object, dịch vụ AWS | MinIO, Azurite, fake-gcs-server, LocalStack |
| Message broker | Kafka, RabbitMQ, MQTT chạy với cấu hình nhỏ |
| Email | Mailpit (test đọc được thư đã gửi) |
| Camera, luồng video | MediaMTX + FFmpeg phát video mẫu |
| FTP/SFTP, LDAP/AD, database của hệ thống khác, thiết bị | SFTP server, OpenLDAP, cùng loại database có dữ liệu mẫu, WireMock hoặc trình giả lập giao thức |

Kịch bản của bản giả (thành công, lỗi 4xx/5xx, chậm/timeout, dữ liệu biên) nằm trong repo và được review như code. Thành phần do chính repo xây (ví dụ gateway) không cần bản giả, chỉ cần ghi lý do. Ở cổng Onboarding, mục **Every external system of SDS section 13 has a test double** báo vàng khi còn tích hợp chưa có bản giả hay lý do; không chặn duyệt. Muốn gọi sandbox thật của bên ngoài: chưa hỗ trợ (BACKLOG 3.7).

## 8. Feature run (một user story)

1. **New request → Feature request**: chọn dự án và **User story** trong SRS §7, điền Desired outcome, bấm **Submit intent.md**.
2. **Cổng Spec (PO):** BA viết AC-01… và EC-01… dạng Given/When/Then. Xem checklist, tick, **Accept**.
3. **Cổng Plan (Tech Lead):** task, file sửa, ma trận tiêu chí × task × test case, kết quả grounding (plan không được nhắc tới file hay hàm không tồn tại). **Approve plan**.
4. **Cổng Quality (QC):** `test_cases.md` và phân tích chéo. **Approve** thì bắt đầu viết code.
5. **Code (tự động):** với mỗi task, QA Engineer viết test, red check (test phải trượt), Developer viết code, validation trong container (phải pass). Theo dõi ở tab **Tasks** và **Diff**.
6. **Acceptance** (khi dự án có `preview` với lệnh `e2e`, và `stages.acceptance` khác `skip`): QA Engineer viết test **Playwright** cho từng AC/EC (tên test bắt đầu bằng mã tiêu chí), nền tảng bật **preview** của nhánh run và chạy test ngay cạnh nó; mỗi tiêu chí có kết quả, ảnh chụp, video.
7. **Cổng QC Acceptance** (`stages.acceptance: gated`, mặc định): khung Preview có **Try it** và tài khoản demo theo vai để QC tự bấm thử; bảng tiêu chí → test → kết quả → ảnh/video (bấm ảnh để phóng to); controller kiểm mọi tiêu chí có test và đều đạt. **Accept the story** → mở PR. **Send defects to the Developer** (ghi đã làm gì, thấy gì, mong đợi gì) → nền tảng thêm **một task sửa lỗi** (Tn+1) được sửa mọi file của plan: QA viết hoặc sửa test cho từng lỗi (phải fail trước), Developer sửa code, rồi Review và Acceptance lại. Các task đã pass không chạy lại. Code Owner **Request changes** trên PR cũng đi đường này. **Run the tests again** (ghi lý do nếu test viết sai, QA viết lại). `auto`: đạt hết thì tự mở PR, có tiêu chí trượt thì dừng ở cổng QC. `skip`: như trước.
8. **Cổng PR (Code Owner):** `review.md` của Code Reviewer chỉ để tham khảo. Tick checklist, mở PR trên GitHub, merge. Run thành `COMPLETED`.

Developer báo test sai (`DISPUTE`), hoặc một bước lỗi, thì run dừng ở `NEEDS_HUMAN`: chọn **Let QA update the tests**, **Keep the tests**, **Resume**, **Replan** hoặc **Cancel run**, kèm lý do.

### Khi một cổng chưa đạt (mọi loại run)

Ở cổng nào cũng có ba hướng: **tự sửa** (Edit, lưu thành phiên bản mới của người), **yêu cầu AI làm lại** (Reject kèm lý do), hoặc **huỷ**. Lý do anh ghi được gửi nguyên văn cho vai AI ở lần làm lại, cùng bản nháp trước của nó, để nó sửa đúng chỗ và giữ nguyên ID.

Job lỗi (hết thời gian, máy ngủ, hết hạn mức) được **tự thử lại** tối đa 3 lần, cột Attempts trên bảng Jobs cho biết đang ở lần thứ mấy. Lần thử lại **chạy tiếp từ bước đã xong**: các lượt SDS và các phần tài liệu đã đọc xong được dùng lại nếu đầu vào và prompt không đổi; log ghi "reused the result of an earlier attempt". Khi job đang chạy, hãy cắm sạc và đừng gập nắp máy Mac: gập nắp thì máy vẫn ngủ dù worker đã giữ `caffeinate`.

Với spec, SRS và SDS, bản viết lại có khung **Changes since vN** ngay trên tài liệu ở cổng duyệt: **What you asked** (các lý do từ chối, hoặc Request changes trên PR, kể từ bản trước), **How the AI role says it handled it** (với mỗi điểm góp ý: AI đã đổi gì hoặc vì sao không đổi, kèm các mục bị ảnh hưởng; SDS gộp câu trả lời của cả 4 lượt), **Sections that changed** (mục nào đổi, số dòng thêm/bớt, đổi nhiều nhất xếp trước) và nút **Show the diff with vN** để xem từng dòng. Câu trả lời của AI chỉ là lời tự khai: hãy đối chiếu với diff. Nếu AI không trả lời, khung báo vàng. Góp ý của người duyệt luôn thắng phương án đã chọn và bản nháp trước (ví dụ bỏ một công nghệ của PA1).

| Cổng | Tự sửa | Yêu cầu AI làm lại | Ai nhận góp ý |
|---|---|---|---|
| Baseline (Discovery) | Ghi quyết định ở tab Decisions, rồi Extract again | **Back to clarifying** | BA, qua quyết định |
| SRS / SDS | **Edit** → Save as new version | **Reject: rewrite**; sau khi đã mở PR: **Request changes** | BA / Architect, kèm bản nháp mới nhất |
| Options (SDS) | Chọn phương án và ghi lý do | **Ask for other options** | Architect |
| Spec (feature) | **Edit** | **Reject: rewrite** | BA, kèm spec trước |
| Plan | **Edit** (JSON) | **Reject: replan** | Tech Lead |
| Test cases (Quality) | **Edit JSON** | **Reject: redesign tests**, hoặc **Reject: replan** | QA Analyst, hoặc Tech Lead |
| Bất kỳ lần dừng nào của một task (khiếu nại test, red check, QA không hiểu quyết định, task fail) | Khung Needs human có **Brief for the Tech Lead** do Code Reviewer (AI, chỉ đọc) viết sau 1–3 phút: chuyện gì xảy ra, mỗi bên nói gì kèm bằng chứng (file:dòng, test case, commit), quyết định khuyến nghị + độ chắc chắn + rủi ro, việc nên kiểm. **Use this reason** điền sẵn lý do; anh vẫn tự chọn nút. **Ask for a brief / Ask again** để hỏi lại. Tắt bằng `SDLC_ADVICE_ENABLED=false` | Tech Lead |
| Red check: test mới đã pass trước khi code | Code đã có sẵn hành vi đó (ví dụ plan trước của run đã làm) | **Already built: keep tests as guards** (ghi lý do): test giữ làm regression guard, bỏ qua Developer, validation quyết định; hoặc Replan | Validation Gate |
| Acceptance (QC) | Bấm thử trên preview | **Send defects to the Developer**, hoặc **Run the tests again** (kèm lý do nếu test sai) | Developer, hoặc QA Engineer |
| PR (feature) | Sửa trên GitHub | **Request changes** (đội sửa code tiếp; ghi chú tới Developer) hoặc **Review again** | Developer, Code Reviewer |

**Viết góp ý thế nào cho hiệu quả:** nêu *cái gì sai*, *ở đâu* và *muốn thành thế nào*. Ví dụ: *"Tách story Check-in và Check-out thành 2 story; bỏ yêu cầu xuất Excel khỏi §3 vì REQ-045 đã ngoài phạm vi"* hiệu quả hơn nhiều so với *"chưa đúng"*. Mỗi ý một dòng; dẫn REQ id, AC id hay số mục SRS khi có thể. Góp ý của mọi lần làm lại trước vẫn được gửi kèm, nên không cần nhắc lại.

### Preview: chạy thử ứng dụng của một run

Dự án có khối `preview` trong `.sdlc/project.yaml` (bước Onboarding của run Foundation viết, kèm `.sdlc/preview.compose.yml`) thì trang run có khung **Preview**: **Start preview** dựng ứng dụng từ nhánh của run bằng docker compose (app, database, bản giả, dữ liệu mẫu), mở **chỉ trên 127.0.0.1** ở một cổng trong `SDLC_PREVIEW_PORTS` (mặc định 9100–9199), rồi hiện **Try it** và bảng tài khoản demo. Preview tự tắt sau `SDLC_PREVIEW_TTL_MINUTES` (mặc định 60), khi run kết thúc, hoặc khi quá `SDLC_PREVIEW_MAX_RUNNING` (mặc định 2) thì bản cũ nhất tắt. Preview lỗi không làm dừng run. Nút **Start preview** chỉ có khi run đang chờ người (một cổng duyệt hoặc Needs human): lúc team đang code thì nhánh còn thay đổi, và lúc Acceptance chạy thì preview do test bật và dùng (không Start/Stop được). Preview lỗi thì khung Preview ghi service nào dừng với mã lỗi và cuối log của nó (ví dụ `No module named app.seed_demo`), và không để container nào chạy lại. Preview của Foundation chỉ gồm những gì Foundation xây (database, bản giả, migrate + seed, api, web); service của story sau vào preview cùng story đó. Tech Lead lập plan có đọc file compose của preview và phải tạo mọi module, seed tài khoản demo, file được mount mà compose chạy. Nếu chính file onboarding sai (ví dụ compose chạy thứ chưa ai xây), ở Needs human của run Foundation bấm **Back to onboarding** kèm lý do. Preview có mạng như khi lập trình viên chạy app trên máy; chỉ dùng dữ liệu và mật khẩu test.

```yaml
preview:
  compose: .sdlc/preview.compose.yml
  service: web            # service mở ra 127.0.0.1
  port: 8080              # cổng trong container của service đó
  path: /                 # trang Try it mở
  health_path: /health    # chờ trả 200
  accounts:
    - {role: HR_ADMIN, username: hr@example.test, password: Demo-123}
  e2e: [npx, playwright, test, --config, e2e/playwright.config.ts]
  e2e_image: mcr.microsoft.com/playwright:v1.49.1-noble
  e2e_paths: [e2e/**]
```

Test E2E chạy trong `e2e_image`, cùng mạng với preview, `BASE_URL` trỏ tới service, repo chỉ đọc, `/evidence` ghi được (báo cáo JSON `/evidence/results.json`, ảnh, video). Preview của `main` và regression trên `main`: BACKLOG 3.8.

## 9. Các trang của control room

| Trang | Dùng để |
|---|---|
| **Inbox** | Mọi việc đang chờ người, theo vai; bấm **Open and decide** |
| **New request** | Tạo Feature, Discovery, Project SRS, Project SDS |
| **Runs** | Danh sách run, lọc theo dự án. Trên trang một run, bấm tên dự án ở breadcrumb để xem các run của dự án đó |
| **Projects** | Thêm dự án, xem kết quả kiểm tra onboarding, **Check again** sau mỗi lần merge |
| **Reports** | Số lần gọi, thời gian, mức dùng (ước tính giá API) theo vai và engine |
| **Evals** | Kiểm tra hồi quy cho vai suy nghĩ trên bộ đề cố định trong `evals/`, chỉ tới bước plan. Hiện cần đăng ký lại dự án `inventory`/`task-board` và chọn engine Claude (ô mặc định còn ghi `qwen3`); xem `docs/BACKLOG.md` |
| **Health** | Trạng thái database, migration, worker |

Trang một run gồm: thanh pipeline, panel **your decision** (việc đang chờ bạn), các tab nội dung, và khung bên phải (PR, base commit, branch, engine). Mẹo: ⌘K để nhảy tới run hay trang bất kỳ; nút mặt trăng đổi giao diện sáng/tối.

## 10. Engine và hạn mức

- Hết hạn mức Claude: đặt `SDLC_DISCOVERY_ENGINE=codex-readonly` để Discovery chạy bằng Codex. Muốn đổi cả vai khác thì sửa `SDLC_ENGINES`, `SDLC_DEFAULT_ENGINE` và `SDLC_TEAM_MODE`. Sau đó `./sdlc down && ./sdlc up`. Từ 17/10 mọi vai đang chạy Codex; quay lại Claude thì đặt ba khoá đó về `claude-readonly`.
- Codex đọc PDF qua `pdftotext` (mỗi dòng gắn `[page N]`, nên vẫn trích được trang). Codex **không đọc được ảnh và PDF scan**: gặp loại đó job sẽ dừng và báo lý do. Codex chỉ nhận chữ nên đọc bảng, sơ đồ kém hơn Claude, và tách mục chi tiết hơn.
- Run đang mở giữ engine lúc tạo. Muốn đổi engine của run đang mở thì hiện phải nhờ người vận hành (xem `docs/BACKLOG.md`, mục 2.6).

## 11. Prompt: xem và tuỳ chỉnh

**AI nhận được gì.** Mỗi lần gọi AI gửi một prompt gồm hai phần:
1. **Hướng dẫn của vai**: file `langflow/prompts/<vai>.md` trong repo nền tảng (ví dụ `extract.md`, `consolidate.md`, `srs.md`, `options.md`), cộng `common.md`.
2. **Dữ liệu JSON của đúng run đó**: bảng requirement, câu hỏi, quyết định, SRS… Kiến thức của dự án (`AGENTS.md`, `.specify/memory/constitution.md`, `REVIEW.md`) nằm trong phần JSON này.

**Xem prompt.** Tab **Jobs, logs & decisions** → nút **Prompt** trên dòng của job. Mỗi lần gọi trong job (từng lượt đọc, lượt consolidate, mỗi lần thử lại) là một prompt, xem được nguyên văn và **Copy** được. Mã **prompt version** (hash của hướng dẫn) cho biết run đã chạy bản hướng dẫn nào. Job chạy trước ngày 17/10/2026 chưa lưu prompt.

**Tuỳ chỉnh.** Hệ thống chia hai lớp, giống cách Claude Code (`CLAUDE.md`), Codex (`AGENTS.md`), GitHub Copilot (`.github/copilot-instructions.md`) hay spec-kit (`constitution.md`) đang làm:

| Lớp | Ai sửa | Sửa ở đâu | Áp cho |
|---|---|---|---|
| Kiến thức và luật của dự án | Đội dự án, qua PR | `AGENTS.md`, `.specify/memory/constitution.md`, `REVIEW.md` trong repo dự án | Mọi vai, chỉ dự án đó |
| Hướng dẫn lõi của từng vai | Đội nền tảng | `langflow/prompts/*.md` trong repo nền tảng | Mọi dự án |

- Muốn AI dùng đúng thuật ngữ, vai, quy ước hay luật nghiệp vụ của dự án: ghi vào `AGENTS.md` hoặc `constitution.md` của dự án. Đây là cách nên dùng.
- Hướng dẫn lõi quy định định dạng đầu ra (JSON theo schema mà controller kiểm), quy tắc trích nguồn và an toàn. Sửa sai một dòng có thể làm hỏng mọi run, nên không sửa trên giao diện. Sửa trong repo nền tảng, rồi kiểm bằng Evals trước khi dùng.
- Chưa có hướng dẫn riêng *theo vai* cho từng dự án (ví dụ thêm lưu ý chỉ cho BA của một dự án): xem `docs/BACKLOG.md`, mục 2.9.

## 12. Giới hạn cấu hình được

Không có giới hạn nào cố định trong code. Giới hạn theo dự án nằm trong `.sdlc/project.yaml`, được ghim vào run lúc preflight, nên **sửa file chỉ ảnh hưởng run mới**.

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `limits.thinking_timeout_minutes` | 10 | Một lần gọi vai suy nghĩ |
| `limits.session_timeout_minutes` | 20 | Một session viết test hoặc code |
| `limits.max_format_retries` | 2 | Số lần hỏi lại khi AI trả sai định dạng |
| `limits.max_remediations` | 2 | Số lần Developer sửa khi validation trượt |
| `limits.max_tasks`, `max_changed_files` | 8, 30 | Kích thước một plan |
| `discovery.limits.max_rounds` | 10 | Số vòng extract trước khi PO phải xác nhận thêm |
| `discovery.limits.max_questions_per_round` | 30 | Số câu hỏi mới mỗi vòng, câu chặn trước |
| `discovery.limits.max_source_mb`, `max_pages_per_source` | 50, 300 | Kích thước một nguồn |
| `discovery.limits.pages_per_read` | 10 | Số trang PDF một lượt đọc (tối đa 20); lượt nào câu trả lời quá dài thì tự chia đôi |
| `discovery.limits.chars_per_read` | 30.000 | Số ký tự văn bản một lượt đọc |
| `architecture.hosting_platforms` | aws, azure, gcp, on_premise, hybrid | Nền tảng hosting Architect được đề xuất ở bước Options; thêm nền tảng khác như `alibaba_cloud` (xem mục 7) |
| `architecture.max_hosting_variants` | 3 | Số biến thể hosting tối đa cho mỗi phương án |
| `discovery.question_owners` | xem thiết kế | Loại câu hỏi → vai trả lời |
| `discovery.commit_sources` | `true` | File thô vào `requirements/sources/` |

## 13. Xử lý sự cố

| Thấy gì | Làm gì |
|---|---|
| Job chờ lâu, log ghi "subscription limit" | Hết hạn mức; job tự chạy lại sau 15 phút. Hoặc chuyển engine (mục 10) |
| Job chậm bất thường, log đứng yên lâu | Máy có thể đã ngủ; giữ máy mở. Lệnh `pmset -g log` cho biết lúc nào máy ngủ |
| "nothing new since inventory vN" | Chưa có nguồn, biên bản hay câu trả lời mới. Dùng **Check the inventory again** nếu chỉ muốn BA kiểm lại |
| "…extraction round(s), the project's limit" | Ghi lý do vào ô **Why another round** rồi bấm **Confirm another round**; hoặc chốt baseline luôn |
| "cannot read PDF/IMAGE sources" hoặc "no text could be read" | BA đang chạy Codex mà nguồn là ảnh hay PDF scan. Chuyển BA của Discovery sang `claude-readonly`, hoặc bỏ nguồn đó |
| Cổng baseline: "… has no function" | Feature chưa có function: trả lời câu hỏi về nó, hoặc ghi quyết định đưa nó ra ngoài phạm vi, rồi Extract again |
| Run ở `NEEDS_HUMAN` | Đọc lý do trong khung đỏ, chọn **Resume**, **Replan** hoặc **Cancel run** kèm lý do |
| Dự án báo **not ready** | Mở dòng lỗi ở Projects, sửa trong repo, push, **Check again** |
| Giao diện không đổi sau khi cập nhật | Tải lại trang (Cmd+R) |
| "Something went wrong · Internal Server Error", doctor báo postgres "recovery mode" | Ổ đĩa ảo của Docker Desktop đầy (log PostgreSQL: `No space left on device`). Giải phóng: `docker builder prune -f`, xoá image không dùng; hoặc Docker Desktop → Settings → Resources → tăng Disk usage limit. PostgreSQL tự khôi phục khi có chỗ |
| Run Foundation dừng: "Docker has no disk space left" | Như trên, rồi **Resume** để build lại môi trường |
| Sau khi đổi cài đặt hay khởi động lại Docker Desktop, doctor báo postgres "Connection refused" | Docker dừng các container khi khởi động lại: chạy `./sdlc up` (dữ liệu vẫn nằm trong volume) |

## 14. Dữ liệu ở đâu

| Lưu gì | Ở đâu |
|---|---|
| Trạng thái run, tài liệu, câu hỏi, quyết định, event (lịch sử mọi thao tác) | PostgreSQL `controller-db` |
| Tài liệu của run | Commit trong `.sdlc/runs/<run>/` trên branch `sdlc/run-<id>` |
| Baseline, SRS, SDS, specs | Repo dự án, qua PR |
| File nguồn của Discovery | `.data/projects/<dự án>/sources/<run>/` (kèm bản văn bản cho Codex) |
| Log từng job | `.data/logs/<run>/jobs/`; xem trực tiếp ở tab **Jobs, logs & decisions** |
| Prompt từng lần gọi AI | `.data/logs/<run>/jobs/<job>.prompts/`; nút **Prompt** ở cùng tab |

Mỗi lần gọi AI là một phiên CLI mới, không lưu hội thoại. Phiên đó chỉ nhận dữ liệu của đúng run, được dựng từ database. Vai suy nghĩ chỉ được đọc; vai viết code chạy trong sandbox; validation chạy trong container không mạng.

## 15. Tài liệu liên quan

- Thiết kế Discovery: [discovery-design.md](discovery-design.md)
- Cách áp quy trình công ty: [aif-mapping.md](aif-mapping.md), hướng dẫn review: [huong-dan-review-aif.md](huong-dan-review-aif.md)
- Kiến trúc và thành phần (sơ đồ): [huong-dan-dung-thu.md](huong-dan-dung-thu.md), mục Kiến trúc
- Vận hành và sự cố chi tiết (tiếng Anh): [runbook.md](runbook.md), [user-guide.md](user-guide.md)
- Trạng thái công việc và việc để sau: `docs/PROGRESS.md`, `docs/BACKLOG.md` ở gốc repo
