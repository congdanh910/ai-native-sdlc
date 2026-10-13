# Option B POC — AI-native SDLC team

Implementation of [AI_Native_SDLC_Option_B.md](../AI_Native_SDLC_Option_B.md), built phase by phase.

| Phase | Status |
|---|---|
| B1 — Foundation: services, tunnel, health checks, `sdlc up/down/doctor` | Done |
| B2 — Durable controller and team skeleton | Next |
| B3 – B7 | Planned |

Start with the [user guide](docs/user-guide.md). Operations and troubleshooting are in the [runbook](docs/runbook.md).

```bash
./sdlc init --gx10-host user@gx10-host --gx10-port 22 --gx10-identity ~/.ssh/gx10
./sdlc up
open http://127.0.0.1:8770
```

Run the tests with `uv run pytest -q`. Integration tests need the local database:
`SDLC_TEST_DATABASE_URL=postgresql+psycopg://sdlc:<password>@127.0.0.1:55442/sdlc uv run pytest -q -m integration`.
