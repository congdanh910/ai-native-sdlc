# Foundation run và môi trường dự án

_2026-10-20. Người dùng chọn: (1) sửa nền tảng cho mọi dự án, (2) thêm run Foundation, (3) cập nhật file onboarding
cũng đi qua run, không làm tay._

## Vấn đề

Sau khi SDS merge, repo dự án vẫn ở trạng thái onboarding: `AGENTS.md` chưa có stack, `.sdlc/project.yaml` chỉ chọn
được một toolchain của nền tảng (`node-20/22`, `python-3.12/3.13`), container validation không có database, và mỗi run
phải gắn với một user story nên không có chỗ dựng khung dự án. Attendance cần Python 3.13 + Node + PostgreSQL 17.

## Giai đoạn 1: môi trường validation theo dự án

`.sdlc/project.yaml` có thêm khối `environment` (tuỳ chọn, dự án cũ không đổi):

```yaml
environment:
  dockerfile: .sdlc/validation.Dockerfile   # nền tảng build (có mạng), tag sdlc-env-<project>:<sha12 của file>
  setup_inputs: [backend/requirements*.txt, web/package-lock.json]   # đổi thì chạy lại setup
  env: {DATABASE_URL: "postgresql://app:app@postgres:5432/app"}
  services:
    - name: postgres                      # tên máy trong mạng của lần validation
      image: postgres:17-alpine
      env: {POSTGRES_USER: app, POSTGRES_PASSWORD: app, POSTGRES_DB: app}
      ready: [pg_isready, -U, app]
      ready_timeout_seconds: 60
```

- Có `dockerfile` thì không cần `validation_image`. Image build một lần theo nội dung file, build context là thư mục
  chứa Dockerfile.
- Mỗi lần chạy validation: tạo mạng Docker `--internal` (không ra Internet), chạy các service, chờ lệnh `ready` thành
  công, chạy lệnh validation trong mạng đó với `env`, xong thì xoá service và mạng.
- Setup (cài dependency, có mạng, ghi vào worktree) chạy lại khi nội dung các file `setup_inputs` hoặc lệnh setup đổi.
- Kiểm readiness của dự án dùng đúng cơ chế này.

## Giai đoạn 2: run Foundation

Loại run mới `foundation`, cần `SDS.md` trên nhánh mặc định, không gắn user story. Các bước:

1. **Onboarding** (Architect, cổng Tech Lead): từ SDS, viết lại `AGENTS.md`, `REVIEW.md`, phần kỹ thuật của
   `.sdlc/project.yaml` (paths, validation, setup, environment) và Dockerfile của môi trường. Controller giữ nguyên các
   phần khác của project.yaml (project, executors, limits, stages, discovery, architecture) và kiểm file mới hợp lệ.
   Cổng hiện diff từng file với nhánh mặc định. Duyệt thì controller commit các file này lên nhánh của run (vai AI viết
   code vẫn không được sửa chúng), build image và chạy thử service; lỗi thì quay lại Architect kèm log. Sau đó run ghim
   lại policy, AGENTS.md, REVIEW.md từ nhánh của run.
2. **Spec** (BA, cổng PO): spec của khung dự án từ SDS: cấu trúc module §4.3.5, cấu hình, migration đầu tiên, health
   check, đăng nhập và phân quyền nền của §7 mà mọi story cần. Không làm chức năng của story nào.
3. **Plan, test cases, code, review, PR**: như run story.

Merge PR của Foundation thì Projects → Check again chạy validation mới trên nhánh mặc định; từ đó các run story dùng
stack và môi trường mới.
