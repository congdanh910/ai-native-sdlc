# Runbook

## Components and ports (loopback only)

| Component | Where | Port |
|---|---|---|
| Controller API and control room | uvicorn process started by `sdlc up`, log `.data/log/api.log` | `SDLC_API_PORT` (8770) |
| Team worker | `python -m controller.app.worker` started by `sdlc up`, log `.data/log/worker.log` | none |
| Controller PostgreSQL | compose service `controller-db`, volume `controller-postgres` | `SDLC_DB_PORT` (55442) |
| Langflow | compose service `langflow`, volume `langflow-data` | `LANGFLOW_PORT` (7870) |
| Langflow PostgreSQL | compose service `langflow-db`, volume `langflow-postgres` | not published |
| SSH tunnel to GX10 Ollama | `ssh -N -L 127.0.0.1:OLLAMA_TUNNEL_PORT:127.0.0.1:OLLAMA_REMOTE_PORT` | `OLLAMA_TUNNEL_PORT` (11435) |

The compose project is `ai-native-sdlc-v3`. It does not share containers or volumes with the removed v1 pilot (`ai-native-sdlc-option-b`).

## Local data

| Path | Content |
|---|---|
| `.data/projects/<key>/mirror.git` | Bare mirror of each project; fetched before every run |
| `.data/worktrees/<run-id>` | Working tree of each run on branch `sdlc/run-<id>` |
| `.data/log/`, `.data/run/` | Logs and PID files of the API, worker and tunnel |

Removing a project's mirror while runs exist breaks those runs; there is no project removal in B2.

## Jobs and recovery

Work is queued in the `executions` table and claimed with `FOR UPDATE SKIP LOCKED` under a 120-second lease that the worker renews. If a worker dies:

- reasoning jobs (preflight, spec, plan, grounding, review) are queued again, at most 3 attempts;
- jobs with side effects (coding, release) are never replayed: the execution becomes `unknown`, the task `UNKNOWN`, and the run `NEEDS_HUMAN`. Inspect the run worktree, then decide in the inbox.

A job that finds its run in another state (for example cancelled) is recorded as `discarded`.

## Security of the control room

The API listens on loopback only and has no login, because the POC has one operator. Requests with a Host header other than `127.0.0.1:PORT` or `localhost:PORT` are refused (DNS rebinding), and every write must carry the header `X-SDLC-Client: web`, which other web pages cannot add without a CORS preflight that the API never grants.

## Tunnel ownership

`sdlc up` opens the tunnel only when nothing listens on `OLLAMA_TUNNEL_PORT`. A tunnel you opened yourself is reported as "opened outside sdlc" and is never stopped by `sdlc down`. A tunnel opened by `sdlc` is tracked in `.data/run/gx10-tunnel.pid` and stopped by `sdlc down`. If it drops, `sdlc doctor` fails the tunnel check and `sdlc up` reopens it.

## Troubleshooting

| Symptom | Action |
|---|---|
| `config` fails with placeholders | Edit `.env`; do not rerun `init` (it refuses to overwrite secrets) |
| `docker compose failed` during `up` | `./sdlc logs langflow` or `./sdlc logs controller-db`; Langflow needs up to 5 minutes on first start |
| `SSH tunnel did not open` | The log tail shows the SSH error; test with `ssh -p PORT -i KEY user@host true` |
| `ollama` reports the model missing | Pull it on GX10 or set `OLLAMA_MODEL` |
| `langflow->ollama` fails while `ollama` passes | The tunnel must bind 127.0.0.1 on the Mac; Docker Desktop maps `host.docker.internal` to it |
| `port … is used by another process` | Change `SDLC_API_PORT` in `.env` |
| `gx10-capacity` warns | Another workload uses GX10; model calls will wait (from B3) |
| `worker` fails or runs do not move | `./sdlc logs worker`, then `./sdlc up` |
| A run is `NEEDS_HUMAN` | Its reason is on the run page; decide in the inbox |

## Reset

`./sdlc down` keeps all data. To delete the POC databases and Langflow state as well:

```bash
./sdlc down
docker compose --env-file .env down --volumes
```

This cannot be undone.
