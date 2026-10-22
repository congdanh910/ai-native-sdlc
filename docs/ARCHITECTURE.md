# Architecture

Full design: `AI_Native_SDLC_Option_B.md`. This page is the map an agent needs to find code.

## Processes

- **API** (`controller/app/main.py`, routes in `api/routes.py`, JSON views in `api/views.py`): decisions of humans
  (create run, approve, answer, add source, extract …). Every decision runs in one transaction and writes an event.
- **Worker** (`worker.py`): claims queued `executions` (job queue on PostgreSQL, `jobs.py`), renews leases, holds a
  macOS `caffeinate -i` assertion while jobs are queued/running, polls PRs.
- **Coordinator** (`coordinator.py`): each job is load (DB, short) → compute (calls a team role, no DB) → apply
  (re-check run version, store artifacts, transition, queue next). Job kinds in `KINDS`: preflight, specify, plan,
  ground, design_tests, code_task, review, release, extract, options, onboarding, environment, acceptance, preview,
  preview_stop, advise (preview, preview_stop and advise belong to no stage: `KINDS` status None).
  `on_claim` discards a code_task claimed after the run left CODING; a code_task whose job breaks marks its task
  FAILED (`fail_task`), so resume starts it again. Code tasks are queued only when their dependencies passed.
- **Run kinds** (`RunKind`): discovery (raw sources → baseline), srs, sds (Options step with hosting variants, then
  four SDS passes), foundation (onboarding files from the SDS, then the walking skeleton), feature (one SRS story).
- **State machine**: `states.py` (RunStatus, RunKind, transitions, who a waiting run waits for) and
  `state_machine.py` (`transition_run` with optimistic versioning).

## Team roles

- `team/base.py`: `RunContext` (everything a role may read) and the `Team` protocol.
- `team/langflow_team.py`: contracts, retries with format feedback, payloads per role. Prompts in
  `langflow/prompts/<role>.md` + `common.md` (read at every call; no braces allowed).
- `team/readonly.py`: `CliReasoningClient` runs `claude -p` / `codex exec` read-only; `ReadonlyTeam`;
  `RoutingTeam` picks the engine per thinking role from the run's pinned `engines` artifact.
- `team/mock.py`: deterministic roles for tests. `team/composite.py`: thinking + doing teams.
- `doing/`: Codex/Claude sessions for tests and code, validation container, change checks, GitHub PRs. Prompts in
  `doing/prompts/` are read once when the worker starts (`TEMPLATES`), so an edit while it runs never meets code
  that does not fill its placeholders.
- Sandbox (`doing/sandbox.py`): Codex runs under a permission profile (`default_permissions="sdlc"`, not
  `--sandbox`): `:minimal`, the worktree (write; read for thinking roles), tmp, the install prefixes of PATH, the
  worktree's git repository, the CLI's own folders and `SDLC_EXECUTOR_READ_PATHS`; no network. `sandboxed_env()`
  keeps git off `~/.gitconfig` and npm's cache in /tmp. Claude sessions: the `doing/guard.py` hook also blocks Read,
  Glob and Grep outside the worktree. Checked on macOS; Linux not yet.
- Brief (`advise` job, prompt `langflow/prompts/advice.md`, schema `Advice`): when a task stops (dispute, red check,
  failure, QA `UNCLEAR`), the Code Reviewer's engine reads the stop, the task, its approved cases, the last attempt's
  steps, rulings and earlier commits, and the repository; the run page shows situation, sides with evidence, a
  recommended decision from `decisions_for(run)` and a reason ready to use. `POST /runs/{id}/advice` asks again.

## Validation environment (`doing/validation.py`)

- `ValidationGate.run`: image → setup → services → commands. The image is a platform toolchain (`validation_image`)
  or the project's `environment.dockerfile`, built once per content (`docker build --progress=plain`, tag
  `sdlc-validate-env-<project>:<sha12>`).
- Setup runs with network and a writable worktree, again whenever the `environment.setup_inputs` change.
- A validation command may name its own `image` (a toolchain name resolved through `policy.toolchains`, or an image
  reference) and its own `setup`, run in that image once per image and setup pair (markers `setup-<hash>.done`); the
  project's image and setup are only needed for commands without an image.
- `services()`: an `--internal` Docker network (no Internet) per validation, the project's service containers with
  their `ready` command, then removal; commands get `environment.env`. Orphans (containers, networks) are removed
  at worker start (`doing/process.py`).
- External systems are never called from validation: each one of SDS §13 has a test double among the services
  (WireMock, LocalStack, MinIO, Mailpit, MediaMTX …). Controlled outbound calls: BACKLOG 3.7.
- `environment.lock` (`CliDoing.lock`, run at the start of `validate`): after a task changed a `setup_inputs` file,
  the lock command runs with network in the validation image; only setup inputs may change; committed as
  `build(Tn): lock dependencies`; a failure goes to the Developer like a validation failure.
- Red check (`CliDoing.red_check`, `validation.red_check`): any failing command that is not lint makes the tests red.
  `validation[].kind` (`auto` = lint when the name says lint): lint failures never count, and lint errors naming a
  test file of the task's `test(Tn)` commit go back to the QA Engineer. Tests under a manifest the task creates and
  that does not exist yet are red by construction. Tests that already pass: the QA Engineer rewrites once; then the
  Tech Lead may rule `already_built` (tests kept as guards, Developer skipped, validation decides); the run page shows
  the evidence (`views._already_built`).
- Disputes: `amend_tests` (QA may edit existing lines; the decision wins over the approved cases; `UNCLEAR:` sends it
  back as a dispute), `keep_tests`. Rulings name their plan; one stored before that applies only when newer than the
  task's plan.

## Preview and acceptance (`preview.py`, `acceptance.py`, design `poc-option-b/docs/acceptance-design.md`)

- `preview.start`: `docker compose -p sdlc-preview-<run8>` with the project's `preview.compose` and a platform override
  publishing `service:port` on `127.0.0.1:<port>`; waits for `health_path`; artifact `preview` (status, url, port,
  expires_at). Ports from `SDLC_PREVIEW_PORTS`, cap `SDLC_PREVIEW_MAX_RUNNING` (oldest stopped), TTL
  `SDLC_PREVIEW_TTL_MINUTES`; `expire_previews` runs with the worker's PR poll. API `POST /runs/{id}/preview`.
- After the review (`acceptance_enabled`: story or foundation run, `preview.e2e`, `stages.acceptance` not skip) the
  `acceptance` job: QA Engineer writes Playwright tests (`write_acceptance_tests`, prompt
  `doing/prompts/acceptance-tests.md`, commit `test(acceptance):`), the preview starts, `run_e2e` runs them in
  `e2e_image` on the preview network, `parse_report` maps tests to AC/EC ids, evidence under
  `.data/evidence/<run>/<attempt>/` (`GET /runs/{id}/evidence/{path}`), document `acceptance`.
- `run_e2e` runs `e2e_argv(preview)`: a writable copy of the repository without dependency folders, then
  `npm ci` with the e2e folder's lockfile or `@playwright/test@<e2e_image version>` linked in, then the e2e command.
- Tests failing in their own code (`acceptance.authoring_errors`: strict mode violation, SyntaxError, Cannot find
  module …) or a suite that does not run go back to the QA Engineer once, and the suite runs again. A suite committed
  for an earlier preview (compose file changed since) is rewritten; a rewrite may change and drop its own files. The
  prompt allows only BASE_URL and the demo accounts.
- Preview failures name each exited service with the end of its logs and remove the containers; image builds have
  `preview.build_timeout_seconds`. People start or stop a preview only while the run waits for a person
  (`services.preview_locked`); never during ACCEPTANCE_TESTING.
- Gate `acceptance` (QC): accept → PR; reject → `_send_defects`: with an SDS in the worktree and
  `SDLC_DEFECT_CHECK_ENABLED`, the `check_defects` job (Code Reviewer's engine, prompt `defect-check.md`, schema
  `DefectCheck`) rates each note consistent / conflict / not_covered against SDS, SRS and spec; a conflict stops the
  run (`DEFECTS_CONFLICT`, run page `defect_check`) for `defects_revised` (checked again) or `defects_as_written`;
  otherwise `_add_fix_task`: one new task Tn+1 of the plan over every file of
  its tasks, payload `defects`; its QA Engineer writes or updates the tests of the defects (amend), then the Developer
  fixes; review and acceptance again, the acceptance QA gets the QC's notes. PR request_changes does the same. retest
  rewrites the tests with the reason.

## Foundation run (`foundation.py`, design `poc-option-b/docs/foundation-design.md`)

- `onboarding` job (Architect on the Tech Lead's engine, prompt `onboarding.md`): `Onboarding` = AGENTS.md,
  REVIEW.md, .gitignore, CI workflow, Dockerfile, the technical part of project.yaml, test doubles per SDS §13.
  `onboarding_files` keeps the rest of project.yaml and must load; an invalid file goes back to the Architect.
- Gate `onboarding` (Tech Lead): diff per file, controller check of the files, warning when an SDS §13 system has no
  test double. Approval: the controller commits the files (`onboarding_commit` artifact).
- `environment` job: builds the image and starts the services on the run's worktree; when the code the preview builds
  exists, starts the preview once (`_try_preview`). Failure → back to the Architect with the log
  (≤ `limits.max_remediations`); a full Docker disk → NEEDS_HUMAN; a preview build timeout is not the Architect's.
  Success → the run re-pins project_config, effective_policy, agents_md, review_md from the onboarding commit and
  enters the spec stage like a story run (spec → plan → test cases → code → review → PR). Reopened from NEEDS_HUMAN
  (`onboarding_return` artifact: target and the stop), it goes back to the acceptance tests, or to coding the approved
  plan (stopped tasks READY; a disputed task stops again for the person to rule, with the original dispute). A code
  job discarded at claim frees its idempotency key so the task can be queued again.
- The plan input has `preview` (compose file and demo accounts): the plan creates what the compose file runs. The
  onboarding prompt keeps a later story's services out of the foundation's preview and takes demo accounts from the
  seed.

## Documents and gates

- `documents.py`: versioned documents per run (intent, spec, srs, sds, tests, review, inventory …) with SHA-256;
  committed to the run thread `.sdlc/runs/<run8>/` on branch `sdlc/run-<run8>` when `commit_to_repo`.
- `project_docs.py` (SRS/SDS models and rendering), `feature_docs.py` (`specs/NNN-slug/` artifacts),
  `checklists.py` (controller / AI self-check / human-ticked checklist at each gate), `analysis.py`.
- `projects/config.py`: `.sdlc/project.yaml` schema (paths, validation, limits, stages, `discovery:`,
  `architecture:` hosting platforms, `environment:` Dockerfile, services, env, setup inputs).
- Rewrites after a rejection keep `data.revision` (feedback, the role's `feedback_replies`, changed sections);
  `GET /api/runs/{id}/documents/{kind}/diff`.

## Discovery (`controller/app/discovery/`)

- `sources.py`: file intake (hash, page count; DOCX/XLSX/PPTX to Markdown with `[p N]`, `[Sheet!A1]`, `[slide N]`).
- `service.py`: operator decisions (add/remove source, start extraction, rounds limit).
- `extract.py`: parts (PDF page ranges read by the CLI; text slices inline), answer repair, halving a part whose
  answer is unusable.
- `inventory.py`: the generic schema (15 item types, 15 question kinds), merging chunks, consolidation, checks,
  Markdown rendering.
- Prompts `langflow/prompts/extract.md`, `consolidate.md`. UI: `frontend/src/components/Sources.tsx`,
  `Requirements.tsx`, decision panels in `Decision.tsx`.
