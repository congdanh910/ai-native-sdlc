# Runbook

## Components and ports (loopback only)

| Component | Where | Port |
|---|---|---|
| Controller API and control room | uvicorn process started by `sdlc up`, log `.data/log/api.log` | `SDLC_API_PORT` (8770) |
| Controller PostgreSQL | compose service `controller-db`, volume `controller-postgres` | `SDLC_DB_PORT` (55442) |
| Langflow | compose service `langflow`, volume `langflow-data` | `LANGFLOW_PORT` (7870) |
| Langflow PostgreSQL | compose service `langflow-db`, volume `langflow-postgres` | not published |
| SSH tunnel to GX10 Ollama | `ssh -N -L 127.0.0.1:OLLAMA_TUNNEL_PORT:127.0.0.1:OLLAMA_REMOTE_PORT` | `OLLAMA_TUNNEL_PORT` (11435) |

The compose project is `ai-native-sdlc-v3`. It does not share containers or volumes with the removed v1 pilot (`ai-native-sdlc-option-b`).

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

## Reset

`./sdlc down` keeps all data. To delete the POC databases and Langflow state as well:

```bash
./sdlc down
docker compose --env-file .env down --volumes
```

This cannot be undone.
