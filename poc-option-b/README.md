# Option B proof of concept

Python and PostgreSQL own the SDLC lifecycle. Langflow runs only the Qwen3 analysis, planning and review flows.

This directory contains the local controller, Langflow flows, coding and review workers, a CLI, and a browser control room. The pilot has run real Codex executions, isolated validation, Qwen3 review, and GitHub PR/CI reconciliation. See [the runbook](docs/runbook.md) for setup and current pilot results. [Langflow contracts](docs/langflow-contracts.md) define the flow inputs and outputs.

From this directory, start the local API and web UI with `./sdlc serve`, then open `http://127.0.0.1:8765`. Use `./sdlc --help` for the CLI. Both use the same controller and PostgreSQL state. Set `SDLC_CONTROLLER_URL` if you choose a different local port.
