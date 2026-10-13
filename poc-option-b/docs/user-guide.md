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

## Real thinking roles (B3)

Set `SDLC_TEAM_MODE=qwen3` in `.env` and run `./sdlc up` again. The Business Analyst, Tech Lead, QA Analyst and Code Reviewer then run on Qwen3 on GX10 through Langflow. The doing roles follow `SDLC_DOING_MODE` (next section). With `mock`, no model is called.

What changes for you:

- **Draft intent.md with AI** asks the Business Analyst on Qwen3.
- The Tech Lead receives code context: the file list, the functions and constants of every file, and the most relevant files in full, within `CONTEXT_CHAR_BUDGET` characters.
- Every plan goes through the **grounding check** before you see it: files to modify must exist, files to create must not, referenced functions must be defined, paths must be allowed and not protected, every acceptance criterion needs a task and a test case. A failing AI plan is sent back to the Tech Lead once with the findings; if it still fails, you see it with the findings listed.
- Before each model call the worker checks GX10 (free RAM and GPU use). When GX10 is busy, the job waits two minutes and tries again; the run shows the job as postponed.
- A model answer that is not valid JSON for the role's contract is sent back up to twice with the error. After that the run stops in `NEEDS_HUMAN`.

### Prompts and Langflow

The prompts of the five thinking flows live in `langflow/prompts/` and are the source of truth. `./sdlc up` creates or updates the flows `sdlc-v3-*` in Langflow (<http://127.0.0.1:7870>, user `admin`, password `LANGFLOW_SUPERUSER_PASSWORD` in `.env`). You can experiment in the Langflow editor, but `./sdlc doctor` reports a changed flow and the next `./sdlc up` restores it from `langflow/prompts/`. Copy a change you want to keep into the prompt file.

## Evals (B3)

**Evals** runs a suite of fixed features through the thinking roles at a pinned commit, with the spec stage on auto, and stops at the plan gate. Nothing reaches coding, and the experiment runs are cancelled when the eval is scored. Experiment runs do not appear in your inbox.

The `task-board` suite contains the five features of the v1 pilot. Each plan is compared with the plan you approved in the pilot:

| Metric | Meaning |
|---|---|
| Grounding pass, first plan / final plan | Share of cases whose plan passed the grounding check, before and after the one automatic replan |
| File recall | Share of the source files the approved plan touched that the new plan also plans to change; a miss usually means forgotten wiring |
| File precision | Share of planned source files that the approved plan also touched |
| Blocking questions | Cases where the Business Analyst stopped to ask |
| Seconds to plan | From request to plan, including waits for GX10 |

Each eval records a fingerprint (team mode, model, context budget, prompt versions) and compares itself with the previous completed eval of the suite. Run an eval after you change a prompt, the model or the context budget.

To use the suite, register the project once: Projects → Add, key `task-board`, repository `https://github.com/congdanh910/ai-native-product-demo`, branch `sdlc-v3/onboarding`.

## Real doing roles (B4)

Set `SDLC_DOING_MODE=cli` in `.env` and run `./sdlc up` again. After you approve a plan, the team writes tests and code in the run's worktree, task by task in dependency order:

1. **QA Engineer** (Codex or Claude Code, see routing below) writes the task's test cases from the approved test plan. It may only add tests under `paths.tests`: changing a source file, deleting or editing existing test lines, or adding `.skip`/`.only` rejects the session and nothing is kept. The controller commits accepted tests as `test(Tn): …`.
2. **Validation Gate red check**: the task's validation commands run in the project's toolchain container. At least one must fail (the feature does not exist yet), unless every case of the task is marked `expected_pass`. If the tests already pass, they go back to the QA Engineer once with the result; a second failure stops the run in `NEEDS_HUMAN`.
3. **Developer** implements the task in a separate CLI session. It may only change the task's `modify`/`create` files and never tests. If it thinks a test is wrong it answers `DISPUTE: …` and the run waits for you (see *Test disputes*). Accepted code is committed as `feat(Tn): …`.
4. **Validation Gate** runs every validation command. A failure goes back to the Developer with the failing output, at most twice; then the run stops in `NEEDS_HUMAN`.

After all tasks pass, the Code Reviewer (Qwen3) writes `review.md`, and the **Release Engineer** pushes `sdlc/run-<id>` to GitHub and opens a **draft PR** against the project's default branch, with `report.md` as its body. Merge it in GitHub yourself: the platform never merges. The worker checks the PR every minute; the run completes only when GitHub reports a merge of the exact head the team pushed. Closing the PR closes the run. A project registered from a local path keeps a simulated PR.

The run page has a **Steps** tab with each step's role, executor, commit, duration and result, and the PR card shows CI and review status.

### Test disputes

A feature that changes existing behaviour sometimes needs an existing test to change, which neither the QA Engineer nor the Developer may do on their own. The Developer then disputes, and the run card shows two decisions for you as Tech Lead (a reason is required):

- **Let QA update the tests**: the QA Engineer gets one session in which it may edit existing test lines (still no deleted files and no skipped tests), with the dispute and your reason. The red check runs again, then the Developer continues. The changed test files are listed in `report.md` and the PR body for the Code Owner.
- **Keep the tests**: the Developer gets your reason and must make the code pass the tests as they are.

Either way the task continues from its committed tests; nothing is written twice. A plain **Resume** also reuses the task's committed tests.

### Routing and sessions

- The Developer uses `default_executor` and the QA Engineer `testing.qa_executor` (default: the same) from `.sdlc/project.yaml`. Set them to different CLIs for stronger independence.
- Both CLIs run non-interactively under your subscription logins (`codex login` with ChatGPT, `claude` with claude.ai). API key variables are removed from their environment, so no paid API is used by accident.
- Each session is new and sees only its inputs: AGENTS.md, the task, the criteria and its test cases.
- **Claude Code** runs with `--permission-mode dontAsk` and file tools only (no shell), plus a hook that blocks edits outside the role's paths. **Codex** runs in its `workspace-write` sandbox with network off. Neither may commit or push; the controller's checks after each session are authoritative.

### Validation container

`validation_image` in `.sdlc/project.yaml` names a toolchain the platform provides (`node-20`, `node-22`, `python-3.12`, `python-3.13`). Validation runs with `docker run --network none`, the worktree mounted read-only, 2 CPUs, 2 GB memory and dropped capabilities. `setup` commands (for example `npm ci`) run once per run worktree with network and a writable mount. Logs are kept in `.data/logs/<run-id>/`.

### Readiness

With `SDLC_DOING_MODE=cli`, **Projects → Check** runs every validation command on the default branch in the container and checks that your `gh` login can push to the repository. Only a `ready` project can reach coding. `./sdlc doctor` also checks the Codex, Claude Code and `gh` logins.
