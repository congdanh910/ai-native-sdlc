# Option B POC — AI-native SDLC team

Implementation of [AI_Native_SDLC_Option_B.md](../AI_Native_SDLC_Option_B.md), built phase by phase.

| Phase | Status |
|---|---|
| B1 — Foundation: services, tunnel, health checks, `sdlc up/down/doctor` | Done |
| B2 — Durable controller, web control room, mock team | Done |
| B3 — Thinking roles on Qwen3 via Langflow, grounding check, plan-only evals | Done (eval 5: 4/5 planned, all grounded) |
| B4 — Doing roles: Codex/Claude CLI, validation container, real draft PRs | Done (draft PR #8 in the demo repo) |
| B5 — Reliability: cancellation, orphan cleanup, subscription limits, signed webhook | Done |
| B6 — Engines per role, second project (Python), team report, new control room (React) | Done |
| B7 | Planned |

Start with the [user guide](docs/user-guide.md). Operations and troubleshooting are in the [runbook](docs/runbook.md). In Vietnamese: [hướng dẫn dùng thử](docs/huong-dan-dung-thu.md) (flow, architecture, roles, how to try) and the [control room design](docs/ui-design.md).

```bash
./sdlc init --gx10-host user@gx10-host --gx10-port 22 --gx10-identity ~/.ssh/gx10
./sdlc up
open http://127.0.0.1:8770
```

Then add a project in the web control room (Projects) and submit a request (New request). With the default `.env` every AI role is a deterministic mock, so you can walk the whole loop without models or code changes. `SDLC_TEAM_MODE=qwen3` puts the thinking roles on Qwen3; `SDLC_DOING_MODE=cli` lets Codex and Claude Code write tests and code and opens real draft PRs.

Run the tests with `uv run pytest -q`. Integration tests need the local database:
`SDLC_TEST_DATABASE_URL=postgresql+psycopg://sdlc:<password>@127.0.0.1:55442/sdlc uv run pytest -q -m integration`.
