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

3. Open the control room at <http://127.0.0.1:8770>.

## Daily use

| Command | What it does |
|---|---|
| `./sdlc up` | Start or resume everything; safe to run again |
| `./sdlc doctor` | Check every component and explain how to fix failures (`--json` for scripts) |
| `./sdlc logs worker` | Recent log of a component: `api`, `worker`, `gx10-tunnel`, `controller-db`, `langflow-db`, `langflow` |
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
| worker | The team worker runs; without it runs do not move |
| api | The controller API answers |

## Working with the team (B2)

Everything below happens in the web control room. The CLI only starts, stops and checks the environment.

In B2 every AI role is a **deterministic mock**: it reads your intent, spec and the repository file list, but calls no model and changes no code. Use it to learn the loop; B3 puts Qwen3 behind the thinking roles and B4 lets Codex and Claude Code write tests and code.

### 1. Onboard a project (as Platform Admin)

A repository needs three files at its root, at the head of its default branch:

| File | Purpose |
|---|---|
| `AGENTS.md` | What every AI role must know: commands, structure, conventions, what never to touch |
| `CLAUDE.md` | Only `@AGENTS.md`, so Claude Code reads the same knowledge |
| `.sdlc/project.yaml` | Validation commands, allowed and test paths, executors, limits, stage modes |
| `REVIEW.md` (optional) | Review passes and what counts as Important |

Copy them from `examples/task-board/` and adapt them. Then open **Projects → Add a project**, with a key equal to `project:` in `.sdlc/project.yaml` and the repository as an https or SSH URL or an absolute local path. The platform clones a read-only mirror and checks the files.

Readiness is `not_ready` (a file is missing or invalid; requests are refused), `plan_only` (files are valid; validation commands and GitHub access are checked from B4) or `ready`. In B2, mock coding also runs for `plan_only` projects.

### 2. Submit a request (as Product Owner)

**New request** is the `intent.md` form: problem, desired outcome (one line per outcome; each becomes an acceptance criterion), affected users and systems, constraints, open questions. Paste free text and press **Draft intent.md with AI** to have the Business Analyst fill the form, then correct it. Submitting accepts `intent.md`.

Start an open question with `[blocking]` and the Business Analyst will stop and ask you before writing the spec.

### 3. Follow the inbox

The **Inbox** lists what waits for you, grouped by the human role you act in. The badge and the tab title show the count; **Enable browser notifications** to be told when something new arrives.

| Inbox item | What you do |
|---|---|
| Answer the Business Analyst's questions | Answer each blocking question; the spec is written next |
| Review spec.md | Accept it, edit it into a new version (keep criteria as `- AC-1: …`), or reject it (rewrite or cancel) |
| Review plan.md and the test plan | Approve the exact version, edit the JSON into a new version, or reject it (replan or cancel) |
| Start a manual stage | A stage set to `manual` in `.sdlc/project.yaml` waits for you to start it |
| Review the pull request | In B2 the PR is simulated: record a merge, request changes (the team runs coding again) or close it |
| Decide how to continue | The team stopped (a failed task, a test dispute, a lost worker). Resume, replan or cancel, with a reason |
| Classify what went wrong | Required for failed runs and for runs you corrected: category, stage and the role whose output was wrong |

Approvals always name the exact version and SHA-256 you were looking at. If a newer version appeared, the decision is refused and you review again.

### 4. Read the artifact thread

Each run has its own branch `sdlc/run-<id>` in the project mirror, starting at the base commit. Every accepted artifact is committed under `.sdlc/runs/<id>/`: `intent.md`, `spec.md`, `plan.md`, `review.md` and `report.md`. The run page shows each artifact with all its versions, plus tasks, the work each role did, your decisions and the full event log.

To look at the branch yourself:

```bash
git --git-dir .data/projects/<key>/mirror.git log --oneline sdlc/run-<id>
```

### Stage modes

`.sdlc/project.yaml` → `stages` sets each stage to `manual` (waits for you to start it), `gated` (runs, then waits for your acceptance) or `auto` (runs and hands off). The plan stage is always gated, merge is always a human action, and nothing deploys.
