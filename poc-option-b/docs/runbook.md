# Option B runbook

## Current pilot status (2026-10-13)

All five Task Board features completed the Codex workflow, passed GitHub Actions, received explicit PR approval, and merged into their respective pilot or feature-specific benchmark branches. The original benchmark commit remains pinned for every feature, and the demo repo's `main` is unchanged. The local CLI and browser control room are available on loopback port `8765`.

Run `9f967e89-b276-4c5f-b32c-c180f164d431` used the human-approved Qwen3:30B Instruct plan v2, ID `02f0ff6e-281b-4331-972f-b1b9ab4b1e40`, SHA-256 `7ce9b74a0281c07c1b2953c2720228d55131a3f572eac24d30778c61058e2df9`. Four Codex tasks completed and were committed in dependency order. Each passed `npm test`, `node --check src/app.js`, and `node --check src/tasks.js` inside the pinned, no-network Node validation container. The Langflow advisory review returned `pass` with no findings.

[PR #1](https://github.com/congdanh910/ai-native-product-demo/pull/1) targeted `pilot/task-board-baseline` from `sdlc/codex/filter-model-cb895dc06379`. Its tested head is `1d5ad5a472a6cb638f31f6b1f103e2683e27f3a9`. Both required GitHub Actions check runs passed. The user explicitly approved PR #1 after reviewing the pilot outcome; the controller recorded that decision as `conversation-user` at run version 13 and advanced the run to `COMPLETED` version 14. The PR was marked ready and merged with an exact head-commit guard. Merge commit `da88b2a8babe1ad434022b698a8dbb1efbd37fe5` is now the head of `pilot/task-board-baseline`. The repository's `main` was not changed.

The Python suite passes 56 tests. The local controller dashboard passed a Chrome headless layout smoke. Browser interaction testing for the demo Task Board remains unavailable in this environment; code review, isolated Node tests, syntax checks, advisory review, and GitHub Actions passed.

## Architecture and isolation

The local Compose project holds its own PostgreSQL, Langflow, and controller state. The controller records immutable plans, exact-version approvals, audited transitions, task leases, checksummed artifacts, and GitHub PR/CI evidence. Its workers call Langflow for analysis, planning, and advisory review. Langflow sends model requests through a loopback SSH tunnel from Mac port `11435` to the dedicated GX10 Ollama container on loopback port `11437`. The 14B POC container uses GX10 loopback port `11436` and remains separate. No existing application container or volume was modified.

The Codex worker pins the Task Board base commit `ec9cdeb9c5c4e72f279eba3599eb4b31c869f713` and its `AGENTS.md`, uses a disposable integration worktree, serializes tasks per run, commits each task, and checks changed paths before validation. Codex runs with `workspace-write`, network access disabled, and web search disabled. Generated JavaScript runs only in a pinned Node container with no network, a read-only repository mount and root filesystem, dropped capabilities, and CPU/memory/process limits. These are pilot safeguards, not a proof of a complete operating-system sandbox.

The process runner uses argument arrays, bounded output, timeouts, and a separate process group. An expired coding lease becomes `UNKNOWN` and moves its run to `NEEDS_HUMAN`; it is never replayed automatically. Mutating pilot worker scripts require the exact run ID and `--execute`. The signed GitHub `check_run` webhook is implemented locally but no public webhook has been connected. For this pilot, `scripts.reconcile_pilot_ci` verified the exact PR identity and all required GitHub Actions checks through the GitHub API, then recorded an audited CI result. Do not expose the controller or Ollama publicly merely for webhook delivery.

## Local commands

Run from `poc-option-b/`:

```bash
uv sync --locked
uv run pytest -q
uv run python -m scripts.demo_b2
```

### CLI and web control room

With the local PostgreSQL and Langflow services configured and migrations applied, start the controller on a loopback port dedicated to this POC:

```bash
./sdlc serve
```

Open `http://127.0.0.1:8765`. Enter `CONTROLLER_TOKEN` from the local `.env` in the browser form. The token stays in page memory and is sent only to the local controller; reload requires entering it again. The web UI lists runs, shows the full requirement, plan tasks and checksum, task status, events, PR and CI evidence, and supports versioned plan revisions, exact-version plan decisions, and PR approval. Creating a benchmark run from the web UI uses the pinned repository and original base commit. It does not start model or coding workers; use the CLI for those stages. Keep the web server bound to `127.0.0.1` and do not forward this port to the network.

The same workflow can be controlled from a terminal. `show` displays the current plan ID, SHA-256 and run version before a decision; pass its exact SHA-256 when approving or rejecting:

```bash
./sdlc features
./sdlc runs
./sdlc create backup
./sdlc show RUN_ID
./sdlc revise-plan RUN_ID --file corrected-plan.json --actor your-name --reason 'Correct model boundary'
./sdlc approve-plan RUN_ID --plan-sha SHA256 --actor your-name
./sdlc code RUN_ID
./sdlc events RUN_ID
```

Set `GX10_SSH_HOST`, `GX10_SSH_PORT`, and `GX10_SSH_IDENTITY_FILE` in `.env` (or pass matching CLI flags) to run `analyze RUN_ID` and `review RUN_ID`. Both commands invoke the three-sample read-only capacity gate immediately before Langflow inference and refuse to continue on failure. If the same three CDP containers remain unhealthy, pass `--allow-unhealthy cdp-backend --allow-unhealthy cdp-frontend --allow-unhealthy cdp-storefront` after checking their state. `code RUN_ID` processes one approved task; repeat until the run enters `VALIDATING`. Then use `review RUN_ID`, `prepare-base RUN_ID`, `publish-pr RUN_ID`, `reconcile-ci RUN_ID`, inspect the PR and CI evidence, and `approve-pr RUN_ID --actor your-name` after human review. `prepare-base` creates only the feature-specific branch at the immutable benchmark commit, and refuses to overwrite a moved branch. Approval records a decision but does not merge the PR. Every worker command is scoped to the fixed benchmark run and uses the existing `--execute` gate internally. Use `SDLC_CONTROLLER_URL` to point CLI read/decision commands to a different local port.

The in-memory B2 demo ends at `VALIDATING` and does not access GX10. To set up dedicated local services from scratch, copy `.env.example` to `.env`, replace every `CHANGE_ME` value with a locally generated value, and keep `.env` out of Git. Keep `DATABASE_URL` consistent with `SDLC_DB_PASSWORD` and `SDLC_DB_PORT`; use URL-safe database passwords. Then run:

```bash
bash scripts/preflight.sh
docker compose --env-file .env up -d controller-db langflow
uv run alembic upgrade head
./sdlc serve
```

Check `/health/live` and `/health/ready`. Create the three local flows with `uv run python -m scripts.create_langflow_flows --apply`, put the returned IDs into `.env`, and run `uv run python -m scripts.smoke_langflow_transport`. These services use the Option B project and its own volumes. To release Mac resources while retaining POC data, use `docker compose --env-file .env stop langflow langflow-db controller-db`.

## GX10 capacity gate

Immediately before any future model load, run:

```bash
python -m scripts.check_gx10_capacity --host "$GX10_SSH_HOST" --port "$GX10_SSH_PORT" --identity-file "$GX10_SSH_IDENTITY_FILE" --allow-unhealthy cdp-backend --allow-unhealthy cdp-frontend --allow-unhealthy cdp-storefront
```

The gate takes three SSH read-only samples of `free`, `nvidia-smi`, and `docker ps`, requiring at least 64 GiB available RAM, at most 20% GPU utilization, and a stable set of running containers. The three allowlisted CDP containers were already unhealthy before this POC. A capacity gate is an operational check, not a guarantee that another workload cannot be affected.

The 30B POC container `ai-native-sdlc-ollama-30b` has a separate volume, a 10-CPU/48-GiB limit, one parallel model, 8192 context, and GX10 loopback port `11437`. The original 4096-token setting was insufficient for advisory review, so only this POC container was recreated with 8192 context after a fresh passing capacity gate and an audited infrastructure retry. The original `qwen3:30b` thinking tag could not satisfy the strict JSON flow contract; the pilot used `qwen3:30b-instruct`. On the final read-only check, GX10 had 109,981 MiB available RAM, GPU utilization 0%, and no model loaded in the 30B container. Only the same three pre-existing CDP containers were unhealthy.

## Pilot history and next gate

The five fixed feature requests and benchmark manifest are in [`../fixtures/task-board/`](../fixtures/task-board/). The 14B reviewed plan remains a separate unapproved run. The 30B Instruct raw plan v1 placed a pure model rule in `src/app.js`, contrary to pinned `AGENTS.md`; [reviewed plan v2](pilot-30b-reviewed-plan.json) corrected it before human approval. Four Codex tasks produced the merged PR #1. This is one feature pilot, not the full five-feature benchmark or a comparison against Claude.

The second benchmark feature, [due dates](../fixtures/task-board/02-due-dates.md), used the immutable original benchmark commit `ec9cdeb9c5c4e72f279eba3599eb4b31c869f713`. Qwen3:30B Instruct plan v1 proposed Date objects in storage and silently converted invalid input to null, both contrary to the feature contract. The corrected [plan v2](pilot-30b-due-dates-reviewed-plan.json) was explicitly approved by the user. Four Codex tasks completed with isolated validation and no remediation. The first review preflight found the loopback SSH tunnel down; it was restored without changing GX10 applications. A fresh capacity gate passed, and Langflow review returned `pass` with no findings. [PR #2](https://github.com/congdanh910/ai-native-product-demo/pull/2) passed GitHub Actions at head `ca3d1a701aef98b8a246b88e0506cc5b65d59cb5`, received explicit user approval, and merged into its feature-specific branch `pilot/benchmark-base-due-dates` at `9b5908c744d722273ddf04f329ec62f65365f821`. Controller run `c2709e02-fe38-44a2-9353-c3de5e7d4d9e` is `COMPLETED` version 11. Browser UI smoke was unavailable because the computer-use browser surface is disabled in this environment.

The third benchmark feature, [priority and sorting](../fixtures/task-board/03-priority.md), has run `00fbbf81-907f-4fda-a54f-b3c940a30306` on the same immutable base. Model plan v1 omitted explicit create/edit form wiring and included a validation-only task. The corrected [plan v2](pilot-30b-priority-reviewed-plan.json) was explicitly approved by the user. Three Codex tasks completed on first execution; each passed isolated `npm test` and both syntax checks. Langflow 30B review returned `pass` with no findings after a passing capacity gate. [PR #3](https://github.com/congdanh910/ai-native-product-demo/pull/3) passed GitHub Actions on head `5610ca1faf6c735e3d6a99fc505c87575d8ef03e`, received explicit user approval, and merged into `pilot/benchmark-base-priority` at `839601331437221799a6a271a9d8bcc7e1b8ffd5`. The controller run is `COMPLETED` version 11.

The fourth benchmark feature, [Archive](../fixtures/task-board/04-archive.md), has run `56a33b13-67ac-44a9-881e-e7ef5165f562` on the immutable original base. The three-sample GX10 capacity gate passed before Qwen3:30B Instruct analysis and planning. Plan v1 needed stronger done-only archive validation, a distinct restore action, and testable active counts. The corrected [plan v2](pilot-30b-archive-reviewed-plan.json), ID `7791ee48-8456-4a99-bb9a-1ef7cf0f0f4e`, SHA-256 `fe2f057d0167e9e1a0ed62ad3b329d91701443b7741f95b4d6b7b1c162ed02d9`, received explicit user approval. Three Codex tasks completed and passed isolated validation, Qwen review returned `pass` after a fresh capacity gate, and [PR #4](https://github.com/congdanh910/ai-native-product-demo/pull/4) passed GitHub Actions. The user approved the PR, and it merged into `pilot/benchmark-base-archive` at `a0f2907c36e8f3819f7da77b08ef5dce42666c1a`. Controller status is `COMPLETED` version 11.

The fifth benchmark feature, [JSON backup](../fixtures/task-board/05-backup.md), has run `0d1f7bd0-fc77-47c0-825d-2dca1f6ca17a`. It was created with the new CLI. A fresh three-sample gate passed with 109068, 109067, and 109027 MiB available RAM and 0% GPU utilization; Qwen3:30B Instruct analysis/planning completed. Plan v1 referenced a nonexistent `renderBoard` function and wrong localStorage key. The corrected [plan v2](pilot-30b-backup-reviewed-plan.json), ID `4d2f4a71-4a9d-4163-b72e-c342d1bce159`, SHA-256 `68ba07880103988284e8a4936f8023ba73c0be7c3c9c9ee7edef4866ba44e524`, received explicit user approval. Three Codex tasks completed and passed isolated validation. A fresh gate passed with 109089, 109079, and 109074 MiB available RAM and 0% GPU utilization before advisory review, which returned `pass`. [PR #5](https://github.com/congdanh910/ai-native-product-demo/pull/5) passed GitHub Actions at head `62480f8d103e0254444bfa5c45b36e0d384ce744`, received explicit user approval, and merged into `pilot/benchmark-base-backup` at `68b4a45a8e06c420b65fdcb9f6205290defc2c4a`. Controller status is `COMPLETED` version 11.
