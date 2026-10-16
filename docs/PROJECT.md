# Project

**AI-Native SDLC**: a single-operator platform that runs a software team of AI roles, with humans at every gate.
It explores the Claude "AI-native SDLC playbook" and the company AIF-SDLC process (`aif-sdlc.md`,
`aif-review-checklist.md`, `aif-artifact-audit.md`). Later goal: a company system for many projects.

## What it does

From raw requirements to merged code, per project repository:

1. **Discovery** (in progress): raw sources from sales/product (PDF, DOCX, XLSX, PPTX, minutes) → requirement
   inventory with a source for every item → questions per role → decisions → baseline committed by PR.
2. **SRS run**: the Business Analyst writes `SRS.md` (company template); the PO approves; draft PR.
3. **SDS run**: the Architect writes `SDS.md`; approval; draft PR.
4. **Feature run** per user story of SRS §7: spec → plan (Tech Lead) → test cases (QA Analyst, Quality gate)
   → tests first and code (QA Engineer + Developer on Codex/Claude CLI in a validation container)
   → review (Code Reviewer) → draft PR. Humans approve spec, plan, tests and the PR; nothing auto-merges.

## Technology

- Controller: Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL (Docker), a job worker; `uv`.
- Control room: React 19 + TypeScript + Vite + Tailwind (`poc-option-b/frontend`, build committed in
  `controller/web/dist`), served by the API at http://127.0.0.1:8770. UI text is English.
- AI roles: thinking roles on `claude-readonly` (Claude Code CLI, read-only tools); doing roles on Codex CLI
  (`default_executor: codex`) or Claude Code. Langflow + Qwen3 on GX10 exist in code but are off.
- Ops CLI: `poc-option-b/sdlc` (`init`, `up`, `down`, `doctor`, `logs`).

## Projects on the platform (2026-10-16)

| Key | Repository | Note |
|---|---|---|
| `attendance` | github.com/congdanh910/ai-native-attendance-check-demo | onboarded again by the user after the clean slate; no runs |
| `discovery-trial`, `-2`, `-3` | local repos in `poc-option-b/.data/demo-repos/` | Discovery trial on the 3 sample PDFs |

Older demo repos (not on the platform now): `ai-native-product-demo` (task-board), `ai-native-inventory-demo`
(inventory PR #1 still open on GitHub).

## Where the detail is

- Design: `AI_Native_SDLC_Option_B.md` (architecture, state machine, data model, security, phases).
- `poc-option-b/docs/`: `user-guide.md`, `runbook.md`, `huong-dan-dung-thu.md` (Vietnamese guide),
  `ui-design.md`, `aif-mapping.md`, `huong-dan-review-aif.md`, `discovery-design.md`.
- Plan doc "Kế hoạch mở rộng AI-native SDLC" (Claude Docs, has the trackers):
  https://claude.ai/code/artifact/770112ad-5c83-4811-90cc-30845b1619c4
- User guide doc: https://claude.ai/code/artifact/744275bb-8a3e-4b33-b0fb-bc1fb8d0c0d1
