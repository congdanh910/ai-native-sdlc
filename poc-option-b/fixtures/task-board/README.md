# Team Task Board benchmark

Product: a small, offline browser task board for a team. The baseline supports create, edit, move, and delete across To do, Doing, and Done. It uses browser local storage, plain JavaScript modules, Node's built-in test runner, and a GitHub Actions job named `task-board-tests`. No backend, account, external API, or third-party package is required by the app.

Pilot repository: `https://github.com/congdanh910/ai-native-product-demo`.

The prepared baseline diff is [baseline.patch](baseline.patch). Its pinned commit is `ec9cdeb9c5c4e72f279eba3599eb4b31c869f713` on the remote branch [`pilot/task-board-baseline`](https://github.com/congdanh910/ai-native-product-demo/tree/pilot/task-board-baseline). The original remote `main` commit contains only a one-line README and is unsuitable for the five feature tasks.

## Benchmark protocol

Each of the five requests below is an independent feature. Start each Codex and Claude attempt from the **same pinned baseline commit** in a fresh worktree. Do not carry changes from one feature into another. Give both agents the exact same request text and the versioned `AGENTS.md` from that commit. Run `npm test`, syntax checks, and the same manual UI checks. Capture elapsed time, attempts, changed paths, failures, and evidence. No automatic merge.

1. [Search and status filter](01-search-filter.md)
2. [Due dates](02-due-dates.md)
3. [Priority sorting](03-priority.md)
4. [Archive completed tasks](04-archive.md)
5. [JSON backup import and export](05-backup.md)

These features exercise UI, pure model logic, persistence compatibility, validation, and error handling while remaining feasible on a small local machine.
