# Langflow flow contracts (B3 preparation)

Langflow is an inference adapter, not the SDLC controller. The Python worker sends one JSON string as `input_value` to a named flow through `POST /api/v1/run/{flow_id}` with `x-api-key`. Each flow must have exactly one Chat Output whose message text is **strict JSON**, with no Markdown fence or commentary. The controller validates that JSON with Pydantic before changing state.

The three flows to create and export from the pinned Langflow runtime are:

| Flow | Input JSON | Output schema |
|---|---|---|
| Requirements | `requirement`, `repo_context`, `base_commit` | `AnalysisResult`: `summary`, nonempty `acceptance_criteria`, `questions`, `assumptions` |
| Planning | `requirement`, `analysis`, `repo_context`, `base_commit` | `PlanPayload`: nonempty `tasks` with unique IDs, description, dependencies, relative allowed paths, acceptance criteria; acyclic DAG |
| Review | `requirement`, `plan`, `diff`, `test_evidence`, `repo_context` | `ReviewResult`: `verdict` (`pass` or `needs_changes`) and findings with severity, path, message |

The repository context must be read from the pinned commit and checksum-recorded before sending any of these requests. Requirements and repository text are untrusted input. The flow prompts must tell the model to treat them as data and must not allow them to override output format or security rules.

The client in `controller/app/integrations/langflow.py` has a finite timeout and response size cap. It rejects invalid envelopes, non-JSON message text, malformed plans, and non-local Langflow URLs. It does not retry by itself; `reasoning_worker.py` and `review_worker.py` apply bounded retries to temporary transport failures, validate schemas, and record failures in controller state.

The three flow export files are in `langflow/flows/`. A model-free echo flow verified the real Langflow HTTP path; the echo flow was deleted after the check. The Langflow container reached a temporary host-local service at `host.docker.internal`, and the host SSH tunnel reached the GX10 Ollama container's `/api/tags` endpoint. Real inference is pending because the shared Ollama has no `qwen3:30b` and a model call could briefly use resources needed by another running application. Do not treat transport and schema checks as proof of model output quality.

Reference: [Langflow 1.12 flow trigger API](https://docs.langflow.org/api-flows-run).
