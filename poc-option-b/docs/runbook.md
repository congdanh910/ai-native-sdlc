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
| `.data/logs/<run-id>/` | B4 session logs (`Tn-tests-1.log`, `Tn-code-1.log`) and validation logs per command |
| `.data/logs/checks/<project>/` | Validation logs of the readiness check |

Removing a project's mirror while runs exist breaks those runs; there is no project removal in B2.

## Jobs and recovery

Work is queued in the `executions` table and claimed with `FOR UPDATE SKIP LOCKED` under a 120-second lease that the worker renews. If a worker dies:

- reasoning jobs (preflight, spec, plan, grounding, review) are queued again, at most 3 attempts;
- jobs with side effects (coding, release) are never replayed: the execution becomes `unknown`, the task `UNKNOWN`, and the run `NEEDS_HUMAN`. Inspect the run worktree, then decide in the inbox.

A job that finds its run in another state (for example cancelled) is recorded as `discarded`.

## Doing roles (B4)

- A code task is one job: tests, red check, code, validation and remediation run inside it, one CLI session at a time. Sessions time out after 20 minutes and their process group is killed; validation commands use their `timeout_seconds`, and a timed-out container is removed.
- Each session starts by discarding uncommitted leftovers in the worktree, so a crashed session cannot leak into the next one. Committed work stays; inspect `git log` in the worktree before resuming an `UNKNOWN` task.
- Codex has no per-path write rule in its sandbox; Claude Code has the guard hook. In both cases the controller's diff check after the session decides what is kept.
- The release job pushes with `gh auth git-credential` as a one-off credential helper; no token is written to disk or to a command line by the platform.
- Polling: every 60 seconds the worker reads open draft PRs (`gh pr view`) for CI, review and merge state. A merge with a head other than the one the team pushed sends the run to `NEEDS_HUMAN`.

## Security of the control room

The API listens on loopback only and has no login, because the POC has one operator. Requests with a Host header other than `127.0.0.1:PORT` or `localhost:PORT` are refused (DNS rebinding), and every write must carry the header `X-SDLC-Client: web`, which other web pages cannot add without a CORS preflight that the API never grants.

## Langflow flows

`./sdlc up` syncs `langflow/prompts/<role>.md` (plus `common.md`) into the flows `sdlc-v3-intent-draft`, `-spec`, `-plan`, `-test-design` and `-review`. Each flow is Chat Input → Prompt → Ollama (`OLLAMA_MODEL`, temperature 0, `OLLAMA_NUM_CTX`) → Chat Output. The flow description records the prompt version, so `./sdlc doctor` can tell when a flow was edited in Langflow. Prompts must not contain braces, because Langflow treats them as variables.

## Model calls and GX10

Only `SDLC_TEAM_MODE=qwen3` calls the model. Before a call the worker samples GX10 free RAM and GPU use over SSH (read-only) and caches a good result for two minutes. A busy GX10 postpones the job by two minutes without counting an attempt. Transport errors are retried with backoff (10, 20, 40 seconds), at most three attempts. Invalid model output is retried inside the call twice with the validation error, then the run goes to `NEEDS_HUMAN`.

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
