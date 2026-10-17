# Architecture

Full design: `AI_Native_SDLC_Option_B.md`. This page is the map an agent needs to find code.

## Processes

- **API** (`controller/app/main.py`, routes in `api/routes.py`, JSON views in `api/views.py`): decisions of humans
  (create run, approve, answer, add source, extract …). Every decision runs in one transaction and writes an event.
- **Worker** (`worker.py`): claims queued `executions` (job queue on PostgreSQL, `jobs.py`), renews leases, holds a
  macOS `caffeinate -i` assertion while jobs are queued/running, polls PRs.
- **Coordinator** (`coordinator.py`): each job is load (DB, short) → compute (calls a team role, no DB) → apply
  (re-check run version, store artifacts, transition, queue next). Job kinds in `KINDS`: preflight, specify, plan,
  ground, design_tests, code_task, review, release, extract.
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

## Documents and gates

- `documents.py`: versioned documents per run (intent, spec, srs, sds, tests, review, inventory …) with SHA-256;
  committed to the run thread `.sdlc/runs/<run8>/` on branch `sdlc/run-<run8>` when `commit_to_repo`.
- `project_docs.py` (SRS/SDS models and rendering), `feature_docs.py` (`specs/NNN-slug/` artifacts),
  `checklists.py` (controller / AI self-check / human-ticked checklist at each gate), `analysis.py`.
- `projects/config.py`: `.sdlc/project.yaml` schema (paths, validation, limits, stages, `discovery:`).

## Discovery (`controller/app/discovery/`)

- `sources.py`: file intake (hash, page count; DOCX/XLSX/PPTX to Markdown with `[p N]`, `[Sheet!A1]`, `[slide N]`).
- `service.py`: operator decisions (add/remove source, start extraction, rounds limit).
- `extract.py`: parts (PDF page ranges read by the CLI; text slices inline), answer repair, halving a part whose
  answer is unusable.
- `inventory.py`: the generic schema (15 item types, 15 question kinds), merging chunks, consolidation, checks,
  Markdown rendering.
- Prompts `langflow/prompts/extract.md`, `consolidate.md`. UI: `frontend/src/components/Sources.tsx`,
  `Requirements.tsx`, decision panels in `Decision.tsx`.
