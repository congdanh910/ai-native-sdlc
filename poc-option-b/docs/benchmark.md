# Task Board benchmark — measured results

## Scope

The fixed five-feature set and immutable benchmark base are in [`../fixtures/task-board/benchmark.json`](../fixtures/task-board/benchmark.json). All five Codex feature candidates ran and their PRs merged into isolated benchmark branches. Matched Claude executions have not run. Do not treat this as a Codex–Claude comparison.

## Search and filter — Codex, Qwen3:30B Instruct

- Run: `9f967e89-b276-4c5f-b32c-c180f164d431`; base: `ec9cdeb9c5c4e72f279eba3599eb4b31c869f713`.
- Raw model plan required one human correction: move pure filter logic to `src/tasks.js` per pinned `AGENTS.md`. Corrected plan v2 was explicitly approved.
- Four of four coding tasks completed on first execution. Agent task times: 203.518, 38.171, 45.876, and 34.995 seconds; sum 322.560 seconds. These are recorded CLI elapsed times, not total wall-clock workflow time.
- Every task passed all three allowlisted validation commands in a pinned read-only, no-network Node container. No coding remediation attempts were recorded.
- Advisory review initially failed because the 4096-token Ollama context was too small. An audited infrastructure retry after increasing only the POC 30B container to 8192 context returned `pass` with no findings.
- [PR #1](https://github.com/congdanh910/ai-native-product-demo/pull/1) passed both required GitHub Actions check runs, received explicit human PR approval, and was merged into `pilot/task-board-baseline` at `da88b2a8babe1ad434022b698a8dbb1efbd37fe5`. GitHub Actions also passed on that merge commit. The controller run is `COMPLETED`.
- Observed failure category: `INFRASTRUCTURE` at review, corrected by the context change. The raw plan correction is recorded as `plan.revised`; the current controller does not separately classify corrected plans outside `FAILED` or `NEEDS_HUMAN`, so the full correction-category distribution is still incomplete.
- Browser visual smoke and per-task peak RAM measurements were unavailable. GX10 capacity checks and post-inference memory snapshots are in the runbook; they are not a per-task memory metric.

## Due dates — Codex, Qwen3:30B Instruct

- Run `c2709e02-fe38-44a2-9353-c3de5e7d4d9e` used the same immutable base and executor `codex`. Qwen3:30B Instruct returned valid structured analysis and plan v1. Review found two contract errors: it proposed `Date` objects rather than `YYYY-MM-DD|null` storage and silently converted invalid input to null. The [corrected plan v2](pilot-30b-due-dates-reviewed-plan.json) received explicit human approval.
- Four of four coding tasks completed on first execution. Recorded agent times were 77.425, 55.001, 43.304, and 45.876 seconds; sum 221.606 seconds. No coding remediation attempts were recorded.
- Every task passed `npm test` and both Node syntax checks in the pinned read-only, no-network validation container. The final diff changed five allowlisted files; the integration worktree was clean and `git diff --check` passed.
- The first review preflight failed because the Mac loopback SSH tunnel had stopped. The tunnel was restored, a fresh GX10 capacity gate passed, and 30B advisory review returned `pass` with no findings. No existing GX10 application was changed.
- [PR #2](https://github.com/congdanh910/ai-native-product-demo/pull/2) targeted a feature-specific branch fixed at the original benchmark commit. GitHub Actions `task-board-tests` passed on head `ca3d1a701aef98b8a246b88e0506cc5b65d59cb5`. The user approved the PR, and it merged into `pilot/benchmark-base-due-dates` at `9b5908c744d722273ddf04f329ec62f65365f821`. The controller run is `COMPLETED`.
- Browser visual smoke was unavailable because the computer-use browser surface is disabled. The test suite covers pure date validation, legacy storage, and overdue logic with an injected local date; it does not automate form interaction in a browser.

## Priority and sorting — Codex, Qwen3:30B Instruct

- Run `00fbbf81-907f-4fda-a54f-b3c940a30306` used the same immutable base and executor `codex`. Qwen3:30B Instruct produced valid structured analysis and plan v1. Review corrected missing create/edit selector wiring and replaced a validation-only task with a substantive per-column ordering task. [Plan v2](pilot-30b-priority-reviewed-plan.json) received explicit human approval.
- Three of three coding tasks completed on first execution. Recorded agent times were 71.592, 44.472, and 46.544 seconds; sum 162.608 seconds. No coding remediation attempts were recorded.
- Every task passed `npm test` and both Node syntax checks in the pinned read-only, no-network validation container. The final diff changed four allowlisted files, the integration worktree was clean, and `git diff --check` passed.
- A fresh three-sample GX10 capacity gate passed before 30B advisory review, which returned `pass` with no findings.
- [PR #3](https://github.com/congdanh910/ai-native-product-demo/pull/3) targeted a feature-specific branch fixed at the original benchmark commit. GitHub Actions `task-board-tests` passed on head `5610ca1faf6c735e3d6a99fc505c87575d8ef03e`. The user approved the PR, and it merged into `pilot/benchmark-base-priority` at `839601331437221799a6a271a9d8bcc7e1b8ffd5`. The controller run is `COMPLETED`.
- Browser visual smoke remains unavailable because the computer-use browser surface is disabled. The automated tests cover pure model and per-column ordering scenarios; they do not interact with the browser forms.

## Archive — Codex, Qwen3:30B Instruct

Run `56a33b13-67ac-44a9-881e-e7ef5165f562` used the same immutable base and executor `codex`. Qwen3:30B Instruct returned valid structured analysis and plan v1. Review strengthened the done-only archive invariant, separate restore and delete actions, and deterministic active-count tests. [Plan v2](pilot-30b-archive-reviewed-plan.json) received explicit human approval.

- Three of three Codex tasks completed on first execution with no coding remediation. Recorded task elapsed times (including validation) were 67.186, 60.275, and 46.519 seconds; sum 173.980 seconds. Each passed `npm test` and both Node syntax checks in isolated validation. The final diff changed five allowlisted files and the worktree was clean.
- A fresh three-sample GX10 gate passed before Qwen review: 109085, 109069, and 109069 MiB available RAM; 0% GPU utilization in each sample. Review returned `pass` with no findings.
- [PR #4](https://github.com/congdanh910/ai-native-product-demo/pull/4) passed GitHub Actions `task-board-tests` on head `dcf38f295690d607a8ff6e278417af7827c41ffb`. The user approved it, and it merged into `pilot/benchmark-base-archive` at `a0f2907c36e8f3819f7da77b08ef5dce42666c1a`. The controller run is `COMPLETED` version 11. The repo's `main` was unchanged.
- Browser interaction remains untested in this environment; automated tests cover the pure archive model, persistence, restore, and active counts.

## JSON backup — Codex, Qwen3:30B Instruct

Backup run `0d1f7bd0-fc77-47c0-825d-2dca1f6ca17a` passed a fresh three-sample GX10 gate and Qwen3:30B Instruct analysis/planning. Plan v1 named a nonexistent `renderBoard` function and the wrong localStorage key. The corrected [plan v2](pilot-30b-backup-reviewed-plan.json), ID `4d2f4a71-4a9d-4163-b72e-c342d1bce159`, received explicit human approval. Three of three Codex tasks completed on first execution with no remediation. Recorded task elapsed times (including validation) were 62.473, 46.604, and 76.121 seconds; sum 185.198 seconds. Each passed `npm test` and both Node syntax checks in isolated validation. The final worktree was clean; `git diff --check` passed. A fresh three-sample GX10 gate before advisory review passed with 109089, 109079, and 109074 MiB available RAM and 0% GPU utilization; review returned `pass` with no findings. [PR #5](https://github.com/congdanh910/ai-native-product-demo/pull/5) passed GitHub Actions `task-board-tests` on head `62480f8d103e0254444bfa5c45b36e0d384ce744`. The user approved it, and it merged into `pilot/benchmark-base-backup` at `68b4a45a8e06c420b65fdcb9f6205290defc2c4a`. The controller run is `COMPLETED` version 11; the demo repo's `main` was unchanged.

## Remaining evidence

Matched Claude runs from the same pinned per-feature base are still required for a Codex–Claude comparison. Record all plan corrections and failure categories, validate GitHub CI for each candidate, and report test success, retries, elapsed time, memory, and observable usage only where measured. Preserve the immutable original commit as benchmark input and use feature-specific PR targets.
