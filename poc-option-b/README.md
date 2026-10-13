# Option B POC — AI-native SDLC team

Implementation of [AI_Native_SDLC_Option_B.md](../AI_Native_SDLC_Option_B.md), built phase by phase.

| Phase | Status |
|---|---|
| B1 — Foundation: services, tunnel, health checks, `sdlc up/down/doctor` | Done |
| B2 — Durable controller, web control room, mock team | Done |
| B3 — Thinking roles on Qwen3 via Langflow, grounding check, plan-only evals | Built; real Qwen3 eval pending |
| B4 — Doing roles: Codex/Claude CLI, validation container, real draft PRs | Next |
| B5 – B7 | Planned |

Start with the [user guide](docs/user-guide.md). Operations and troubleshooting are in the [runbook](docs/runbook.md).

```bash
./sdlc init --gx10-host user@gx10-host --gx10-port 22 --gx10-identity ~/.ssh/gx10
./sdlc up
open http://127.0.0.1:8770
```

Then add a project in the web control room (Projects) and submit a request (New request). In B2 every AI role is a deterministic mock, so you can walk the whole loop without models or code changes.

Run the tests with `uv run pytest -q`. Integration tests need the local database:
`SDLC_TEST_DATABASE_URL=postgresql+psycopg://sdlc:<password>@127.0.0.1:55442/sdlc uv run pytest -q -m integration`.
