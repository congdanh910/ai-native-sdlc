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
  preview_stop (the two preview jobs belong to no stage: `KINDS` status None, failures never stop the run).
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
- `doing/`: Codex/Claude sessions for tests and code, validation container, change checks, GitHub PRs.

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
- Gate `acceptance` (QC): accept → PR; reject → every task READY with the QC's notes (code_task gets
  `changes` since the last review, also for PR request_changes), review and acceptance again; retest.

## Foundation run (`foundation.py`, design `poc-option-b/docs/foundation-design.md`)

- `onboarding` job (Architect on the Tech Lead's engine, prompt `onboarding.md`): `Onboarding` = AGENTS.md,
  REVIEW.md, .gitignore, CI workflow, Dockerfile, the technical part of project.yaml, test doubles per SDS §13.
  `onboarding_files` keeps the rest of project.yaml and must load; an invalid file goes back to the Architect.
- Gate `onboarding` (Tech Lead): diff per file, controller check of the files, warning when an SDS §13 system has no
  test double. Approval: the controller commits the files (`onboarding_commit` artifact).
- `environment` job: builds the image and starts the services on the run's worktree. Failure → back to the Architect
  with the log (≤ `limits.max_remediations`); a full Docker disk → NEEDS_HUMAN. Success → the run re-pins
  project_config, effective_policy, agents_md, review_md from the onboarding commit and enters the spec stage like a
  story run (spec → plan → test cases → code → review → PR).

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
