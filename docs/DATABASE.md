# Database

PostgreSQL in Docker (`controller-db`, port 55442 on loopback). Databases: `sdlc` (platform) and `sdlc_test`
(pytest drops and recreates its schema). Models: `poc-option-b/controller/app/models.py`; migrations:
`controller/migrations/versions/` (Alembic, applied by `sdlc up`; head 0010).

| Table | Holds |
|---|---|
| `projects` | registered repos, readiness report, mirror path |
| `runs` | one request: kind (`feature`, `srs`, `sds`, `discovery`), status, version (optimistic lock), branch, worktree |
| `documents` | versioned run documents; `data` JSONB keeps the structured form (e.g. the inventory) |
| `artifacts` | pinned inputs of a run: project_config, effective_policy, agents_md, constitution, engines … |
| `plans`, `tasks` | plan versions with grounding report; tasks of the current plan |
| `executions` | the job queue: kind, role, status, attempts, lease, result, engine |
| `approvals` | human gate decisions with checklist evidence; `stage` up to 32 letters (0010: `onboarding`) |
| `clarifications` | questions; discovery questions have `status` set (open/answered/deferred/withdrawn) plus kind, owner_role, items, citations, round, position (Q-001 order) |
| `decisions` | discovery decisions D-001…: from an answer or from minutes; proposed/accepted/rejected (0008) |
| `sources` | discovery raw files: SHA-256, pages, path, text view, purpose (requirements/minutes), removed flag |
| `pull_requests`, `events`, `eval_runs` | draft PR state, audit trail of every decision and transition, evals |

Conventions:
- Every change of state goes through `transition_run` / `transition_task` and writes an `events` row.
- Nothing is deleted for audit reasons: a removed source keeps `removed = true` and stays on disk.
- Files live under `poc-option-b/.data/` (projects, mirrors, sources, worktrees, logs); not in git.
