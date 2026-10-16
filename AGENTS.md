# Agent instructions (Codex, Claude Code and any other agent)

This repository is the **AI-Native SDLC** platform: a controller (Python + PostgreSQL) that runs a team of AI roles
(Business Analyst, Tech Lead, QA, Developer, Code Reviewer, Architect) with humans approving every gate. Call it
"AI-Native SDLC", not "POC" (the folder `poc-option-b/` keeps its name).

## Before starting work

1. Read `docs/PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`.
2. Read `docs/DATABASE.md` when the data model or migrations are involved.
3. Run `git status` and `git log --oneline -15` (branch `feature/option-b-v2`, draft PR #2).
4. Check the platform: `cd poc-option-b && ./sdlc doctor`; the control room is http://127.0.0.1:8770.

Do not change code until you know where the last session stopped (`docs/PROGRESS.md`).

## Working rules

- Do only the scope asked. Ask before adding files or code to the user's project repositories (attendance,
  inventory, task-board demos).
- No hard-coded tunables: timeouts, retries and limits go in `.sdlc/project.yaml` (per project, `limits:` /
  `discovery.limits:`) or platform settings (`controller/app/config.py`, `.env`), with a documented default.
- Discovery, SRS and SDS logic must stay generic for every project: never tune prompts or code to one sample document.
- The platform never auto-merges and never deploys. No paid API keys: AI roles run on the user's Claude and Codex
  subscriptions through their CLIs. Qwen/GX10 is off (`SDLC_QWEN_ENABLED=false`); ask before any GX10 use.
- Never restart services while a run is in CODING or a job is running (`./sdlc down && ./sdlc up` otherwise).
  Check running jobs first: `curl -s -H 'X-SDLC-Client: web' localhost:8770/api/runs`.
- Real AI calls (extraction, SRS/SDS runs, evals) use the shared subscription quota: test on the smallest sample,
  and ask before re-running all samples.
- Commit messages end with `Co-Authored-By: <model> <noreply@anthropic.com>` when an AI wrote them; push as you go.
- The user's company process files at the repo root (`aif-*.md`) are read-only.

## Tests

```bash
cd poc-option-b
export SDLC_TEST_DATABASE_URL="postgresql+psycopg://sdlc:$(grep ^SDLC_DB_PASSWORD .env | cut -d= -f2)@127.0.0.1:55442/sdlc_test"
uv run pytest -q --deselect controller/tests/unit/test_doing.py::test_validation_runs_in_a_container_without_network
cd frontend && npm run build   # typecheck + build into controller/web/dist (committed)
```

The deselected docker test passes when run alone.

## Before ending a session

Update `docs/PROGRESS.md` (done, unfinished, known problems, tests run, next step) and `docs/DECISIONS.md` for
durable decisions; commit them with the code. The next session must be able to continue without this conversation.
