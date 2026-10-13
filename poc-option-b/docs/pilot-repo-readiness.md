# Pilot repository readiness

Repository: `https://github.com/congdanh910/ai-native-product-demo`

Read-only inspection on 2026-10-11 found HEAD `85312c5b9b633cd5eeec6a61fbc3d8fa6123cfaa` (`Initial commit`). The tree contains only `README.md` with the repository title. There is no product code, test suite, build command, or `AGENTS.md` yet.

This is suitable as a clean pilot repository, but it cannot serve as a benchmark base commit until a small runnable product scaffold and versioned agent context have been added. The baseline should contain the product's core model, a local run command, deterministic tests, and `AGENTS.md` with protected paths and validation commands. Then pin that new commit. For each of the five feature requests, start Codex and Claude from the same pinned baseline (or the same per-task base commit), use isolated worktrees, and compare only measured outcomes.

The pilot repository is separate from this Option B controller. Do not run coding agents against the controller repository by accident.

## Prepared baseline

A Team Task Board baseline was prepared and pushed to branch [`pilot/task-board-baseline`](https://github.com/congdanh910/ai-native-product-demo/tree/pilot/task-board-baseline), commit `ec9cdeb9c5c4e72f279eba3599eb4b31c869f713`. The exact diff from the public `main` baseline is saved as [`../fixtures/task-board/baseline.patch`](../fixtures/task-board/baseline.patch). The clone passed four Node tests and JavaScript syntax checks. Browser visual testing is still pending because the UI automation surface was unavailable in this session.

The pinned `AGENTS.md` SHA-256 is `10612116850a04d18448da270d8d2a69c80e84145131ba1d49b0f25de61879bf`. The branch now exists on the pilot remote, so this commit can be used as the shared benchmark base. GitHub CLI authentication was restored before the feature PR pilot; four feature PRs have since been created and merged into their benchmark branches. Do not place credentials in this repository.
