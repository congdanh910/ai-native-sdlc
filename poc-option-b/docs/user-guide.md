# User guide

This POC is a single-operator AI-native development team. You hold every human role: Product Owner, Tech Lead, Code Owner and Platform Admin. This guide grows with each phase.

## Prerequisites

- macOS with Docker Desktop running
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv if missing)
- SSH key access to GX10, with Ollama listening on a GX10 loopback port and the model `qwen3:30b-instruct` pulled

## First start (B1)

1. Create the local configuration. Secrets are generated; nothing is copied by hand.

   ```bash
   cd poc-option-b
   ./sdlc init --gx10-host user@gx10-host --gx10-port 2222 \
     --gx10-identity ~/.ssh/gx10_key --ollama-remote-port 11437
   ```

   `--operator` defaults to your login name; it is the name recorded on every decision. The file `.env` is created with mode 600 and is ignored by Git.

2. Start everything:

   ```bash
   ./sdlc up
   ```

   This starts PostgreSQL and Langflow in Docker, applies database migrations, opens the SSH tunnel to GX10 (or reuses one that already listens on the tunnel port), starts the controller API, and prints a health report. The first start downloads images and can take a few minutes; later starts take about 30 seconds.

3. Open the control room at <http://127.0.0.1:8770>. In B1 it shows the health of each component and refreshes every 10 seconds.

## Daily use

| Command | What it does |
|---|---|
| `./sdlc up` | Start or resume everything; safe to run again |
| `./sdlc doctor` | Check every component and explain how to fix failures (`--json` for scripts) |
| `./sdlc logs api` | Recent log of a component: `api`, `gx10-tunnel`, `controller-db`, `langflow-db`, `langflow` |
| `./sdlc down` | Stop the API, the tunnel if `sdlc` opened it, and the containers. Data is kept |

## What the health checks mean

| Check | Passes when |
|---|---|
| config | `.env` exists, has no `CHANGE_ME` values, and the GX10 SSH settings are usable |
| docker | Docker Desktop answers |
| postgres, migrations | The controller database is up and at the latest schema |
| langflow | Langflow and its database answer |
| tunnel | Something listens on the tunnel port, either started by `sdlc` or by you |
| ollama | Ollama answers through the tunnel and has `OLLAMA_MODEL` |
| langflow->ollama | Langflow, inside Docker, reaches Ollama through `host.docker.internal` |
| gx10-capacity | GX10 has at least `GX10_MIN_AVAILABLE_MIB` free RAM and at most `GX10_MAX_GPU_UTIL`% GPU use. Only a warning: from B3 the worker waits for capacity before each model call |
| api | The controller API answers |

## Coming next

B2 adds projects, runs and the inbox: you will submit an `intent.md` in the web control room, and the (mocked) team will move it through spec, plan, tests, code and review to a draft PR.
