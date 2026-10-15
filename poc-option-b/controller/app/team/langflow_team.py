"""Thinking roles on Qwen3 through Langflow; doing roles stay mocked until B4.

Every model answer is normalised, then validated against the role's contract. An
invalid answer is sent back once or twice with the validation error, which is safe
because thinking roles have no side effects.
"""

from collections.abc import Callable
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from controller.app import capacity
from controller.app.config import Settings
from controller.app.integrations.langflow import (InvalidModelOutput, LangflowClient, client_from_settings,
                                                  extract_json, role_prompt)
from controller.app.project_docs import Sds, Srs, srs_features
from controller.app.projects.config import matches_any
from controller.app.schemas import Intent, PlanPayload, Review, Spec, TestPlan, parse_criteria
from controller.app.team import normalize
from controller.app.team.base import CallRecord, CapacityBusy, RunContext
from controller.app.team.mock import MockTeam
from controller.app.tunnel import ensure_tunnel, tunnel_status

MAX_FORMAT_RETRIES = 2
UI_SUFFIXES = (".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte")
CAPACITY_CACHE_SECONDS = 120
Model = TypeVar("Model", bound=BaseModel)


class LangflowTeam(MockTeam):
    def __init__(self, settings: Settings, client: LangflowClient | None = None,
                 check_capacity: Callable[[], list[str]] | None = None,
                 check_ollama: Callable[[], None] | None = None):
        self.settings = settings
        self.client = client or client_from_settings(settings)
        self.engine = settings.ollama_model
        self.last_call: CallRecord | None = None
        self._check_capacity = check_capacity or self._gx10_capacity
        self._capacity_ok_until = 0.0
        if check_ollama is not None:
            self._ensure_ollama = check_ollama

    # ------------------------------------------------------------ plumbing

    def _gx10_capacity(self) -> list[str]:
        sample = capacity.sample(self.settings)
        return capacity.evaluate(sample, min_available_mib=self.settings.gx10_min_available_mib,
                                 max_gpu_util=self.settings.gx10_max_gpu_util)

    def _ensure_ollama(self) -> None:
        """Ollama must answer through the tunnel; reopen a tunnel that sdlc manages if it dropped."""
        if self._ollama_reachable():
            return
        if tunnel_status(self.settings).state == "down":
            try:
                ensure_tunnel(self.settings)
            except RuntimeError as exc:
                raise CapacityBusy(f"the SSH tunnel to GX10 is down and could not be reopened: {exc}") from exc
            if self._ollama_reachable():
                return
        raise CapacityBusy("Ollama on GX10 does not answer through the tunnel")

    def _ollama_reachable(self) -> bool:
        try:
            return httpx.get(f"{self.settings.ollama_url}/api/tags", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    def _ensure_capacity(self) -> None:
        self._ensure_ollama()
        if time.monotonic() < self._capacity_ok_until:
            return
        try:
            reasons = self._check_capacity()
        except Exception as exc:  # SSH or parsing problems: do not load a model blindly
            raise CapacityBusy(f"cannot read GX10 capacity: {exc}") from exc
        if reasons:
            raise CapacityBusy("GX10 is busy: " + "; ".join(reasons))
        self._capacity_ok_until = time.monotonic() + CAPACITY_CACHE_SECONDS

    def _ask(self, role: str, payload: dict, contract: type[Model], repair: Callable[[dict], dict],
             session_id: str) -> Model:
        prompt = role_prompt(role)
        started = time.monotonic()
        problems: list[str] = []
        for attempt in range(1, MAX_FORMAT_RETRIES + 2):
            self._ensure_capacity()
            request = dict(payload)
            if problems:
                request["format_feedback"] = problems
            raw = self.client.run(prompt.endpoint, request, session_id=f"{session_id}:{role}:{attempt}")
            try:
                result = contract.model_validate(repair(extract_json(raw)))
                self.last_call = CallRecord(self.engine, prompt.version, attempt,
                                            usage={"seconds": round(time.monotonic() - started, 1)})
                return result
            except (InvalidModelOutput, ValidationError, ValueError) as exc:
                problems = [f"Your previous answer was rejected: {_describe(exc)}",
                            "Answer again with one JSON object that follows the output keys exactly."]
        raise InvalidModelOutput(f"{role}: no valid answer after {MAX_FORMAT_RETRIES + 1} attempts: {problems[0]}", raw)

    def _constitution(self, context: RunContext) -> str:
        """The project's rules. Qwen3's context window is small, so a long constitution is cut to the budget; the
        rules come first in the file, the rationale later."""
        text = context.constitution_md or ""
        budget = self.settings.constitution_char_budget
        return text if len(text) <= budget else text[:budget] + "\n\n[cut: the rest of the constitution did not fit]"

    @staticmethod
    def _policy(context: RunContext) -> dict:
        policy = context.policy
        return {"allowed_paths": policy.allowed_paths, "protected_paths": policy.protected_paths,
                "test_paths": policy.test_paths, "max_tasks": policy.max_tasks,
                "validation": [command.name for command in policy.validation]}

    # ------------------------------------------------------------ thinking roles

    def draft_intent(self, text: str, title: str) -> Intent:
        return self._ask("intent-draft", {"title": title, "text": text}, Intent,
                         lambda data: normalize.intent(data, title), session_id="intent-draft")

    def specify(self, context: RunContext) -> Spec:
        story = None
        if context.story:
            story = {key: context.story[key] for key in ("story_id", "story_title", "actors", "statement",
                                                          "feature_name", "requirements")}
        payload = {"intent": context.intent_md, "story": story, "agents_md": context.agents_md, "constitution": self._constitution(context),
                   "answers": [{"question": q, "answer": a} for q, a in context.answers],
                   "repository": {"files": context.repository.get("files", []),
                                  "symbols": context.repository.get("symbols", {})}}
        return self._ask("spec", payload, Spec, normalize.spec, context.session_id)

    def write_srs(self, context: RunContext) -> Srs:
        payload = {"brief": context.intent_md, "current_srs": context.srs_md or "",
                   "answers": [{"question": q, "answer": a} for q, a in context.answers],
                   "agents_md": context.agents_md, "constitution": self._constitution(context), "feedback": context.feedback}
        return self._ask("srs", payload, Srs, normalize.srs, context.session_id)

    def write_sds(self, context: RunContext) -> Sds:
        stories = [{"id": s.id, "feature": f.prefix, "title": s.title, "actors": s.actors}
                   for f in srs_features(context.srs_md or "") for s in f.stories]
        payload = {"brief": context.intent_md, "srs": context.srs_md or "", "stories": stories,
                   "current_sds": context.sds_md or "", "answers": [{"question": q, "answer": a} for q, a in context.answers],
                   "agents_md": context.agents_md, "constitution": self._constitution(context), "repository": {"files": context.repository.get("files", []),
                                                                  "symbols": context.repository.get("symbols", {})},
                   "feedback": context.feedback}
        return self._ask("sds", payload, Sds, normalize.sds, context.session_id)

    def plan(self, context: RunContext) -> PlanPayload:
        validations = [command.name for command in context.policy.validation]
        # Tests are the QA Engineer's work: without their contents the Tech Lead stops planning test tasks
        # (eval 3). Markup and stylesheets are named separately because Qwen3 kept leaving them out.
        policy = context.policy
        files = context.repository.get("files", [])
        repository = {**context.repository,
                      "selected_files": [f for f in context.repository.get("selected_files", [])
                                         if not matches_any(f["path"], policy.test_paths)],
                      "ui_files": [p for p in files if p.endswith(UI_SUFFIXES) and matches_any(p, policy.allowed_paths)]}
        payload = {"spec": context.spec_md, "agents_md": context.agents_md, "constitution": self._constitution(context), "repository": repository,
                   "policy": self._policy(context), "feedback": context.feedback}
        return self._ask("plan", payload, PlanPayload, lambda data: normalize.plan(data, validations),
                         context.session_id)

    def design_tests(self, context: RunContext, plan: PlanPayload) -> TestPlan:
        criteria = [{"id": c.id, "text": c.full} for c in parse_criteria(context.spec_md or "")]
        # The QA Analyst needs the existing tests and the names it can call, not every source file;
        # a smaller input leaves room in the context window for one case per criterion.
        test_paths = context.policy.test_paths
        repository = {"files": context.repository.get("files", []), "symbols": context.repository.get("symbols", {}),
                      "selected_files": [f for f in context.repository.get("selected_files", [])
                                         if matches_any(f["path"], test_paths)]}
        payload = {"spec": context.spec_md, "criteria": criteria, "plan": plan.model_dump(),
                   "agents_md": context.agents_md, "constitution": self._constitution(context), "repository": repository, "policy": self._policy(context),
                   "feedback": context.feedback}
        return self._ask("test-design", payload, TestPlan, normalize.test_plan, context.session_id)

    def review(self, context: RunContext, plan: PlanPayload, tests: TestPlan, diff: str = "",
               validation: str = "") -> Review:
        # The reviewer needs which case proves which criterion, not every scenario text: with a real diff the full
        # test plan overflowed Qwen3's 8192-token window (inventory run, 2026-10-14).
        cases = [{"id": c.id, "task_id": c.task_id, "criterion": c.criterion, "target_file": c.target_file}
                 for c in tests.cases]
        payload = {"spec": context.spec_md, "plan": plan.model_dump(), "test_plan": {"cases": cases},
                   "diff": diff, "validation": validation or "coding was simulated; no validation ran",
                   "agents_md": context.agents_md, "constitution": self._constitution(context), "review_md": context.review_md}
        return self._ask("review", payload, Review, normalize.review, context.session_id)


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors()[:5]:
            location = ".".join(str(p) for p in error["loc"]) or "object"
            parts.append(f"{location}: {error['msg']}")
        return "; ".join(parts)
    return str(exc).splitlines()[0][:300]
