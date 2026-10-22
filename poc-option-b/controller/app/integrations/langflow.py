"""Langflow as the reasoning adapter: one flow per thinking role, prompts synced from this repository.

Langflow never owns SDLC state. The controller sends one JSON document as the chat
input and expects one strict-JSON chat output, validated with Pydantic by the caller.
"""

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from controller.app.config import PROJECT_ROOT, Settings, get_settings

PROMPTS = PROJECT_ROOT / "langflow" / "prompts"
FLOW_PREFIX = "sdlc-v3-"
ROLES = ("intent-draft", "spec", "srs", "sds-model", "sds-architecture", "sds-interfaces", "sds-operations", "plan", "test-design", "review", "extract", "consolidate", "minutes", "options", "onboarding", "advice", "defect-check", "ui-concepts")


class LangflowError(RuntimeError):
    """Langflow or the model could not be reached or failed."""


class InvalidModelOutput(ValueError):
    """The model answered, but not with the JSON the contract requires."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class RolePrompt:
    role: str
    text: str

    @property
    def version(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]

    @property
    def endpoint(self) -> str:
        return FLOW_PREFIX + self.role


def prompt_text(role: str) -> str:
    """The prompt files of a role as they are now; part of what makes a saved step reusable on a retry."""
    try:
        return role_prompt(role).text
    except (ValueError, OSError):
        return ""


def role_prompt(role: str, prompts: Path = PROMPTS) -> RolePrompt:
    if role not in ROLES:
        raise ValueError(f"unknown role prompt {role!r}")
    text = (prompts / f"{role}.md").read_text().strip() + "\n\n" + (prompts / "common.md").read_text().strip()
    if "{" in text or "}" in text:
        raise ValueError(f"prompt {role} contains braces, which Langflow treats as variables")
    return RolePrompt(role, text + "\n")


def extract_json(text: str) -> dict:
    """Accept the object even when a model wraps it in a fence or adds a stray sentence."""
    stripped = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence[1]
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise InvalidModelOutput("the answer contains no JSON object", text) from None
        try:
            value = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError as exc:
            raise InvalidModelOutput(f"the answer is not valid JSON: {exc.msg}", text) from None
    if not isinstance(value, dict):
        raise InvalidModelOutput("the answer must be a JSON object", text)
    return value


class LangflowClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        timeout = timeout if timeout is not None else get_settings().ollama_timeout_seconds + 60
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Langflow URL must be local HTTP")
        if not api_key:
            raise ValueError("Langflow API key is required")
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers={"x-api-key": api_key},
                                  timeout=httpx.Timeout(timeout, connect=10), transport=transport)

    def _json(self, method: str, path: str, *, limit: int | None = None, **kwargs):
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise LangflowError(f"Langflow request failed: {exc}") from exc
        if limit is not None and len(response.content) > limit:
            raise LangflowError("Langflow response too large")
        if response.status_code >= 400:
            raise LangflowError(f"Langflow HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def run(self, endpoint: str, payload: dict, *, session_id: str) -> str:
        """Run a flow by endpoint name and return the single chat output text."""
        envelope = self._json("POST", f"/api/v1/run/{endpoint}", limit=get_settings().langflow_max_response_bytes, json={
            "input_value": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "input_type": "chat", "output_type": "chat", "session_id": session_id})
        try:
            outputs = envelope["outputs"]
            text = outputs[0]["outputs"][0]["results"]["message"]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LangflowError("Langflow returned an unexpected envelope") from exc
        if len(outputs) != 1 or len(outputs[0]["outputs"]) != 1 or not isinstance(text, str):
            raise LangflowError("the flow must have exactly one chat output")
        return text

    def list_flows(self) -> list[dict]:
        return self._json("GET", "/api/v1/flows/", params={"get_all": "true", "header_flows": "true"})

    def sync_flows(self, model: str, ollama_url: str, num_ctx: int) -> list[str]:
        """Create or update one flow per role so that each runs this repository's prompt."""
        examples = self._json("GET", "/api/v1/flows/basic_examples/")
        template = next(item for item in examples if item.get("name") == "Basic Prompting")
        ollama = self._json("GET", "/api/v1/all")["ollama"]["ext:ollama:ChatOllamaComponent@official"]
        existing = {flow.get("endpoint_name"): flow for flow in self.list_flows()}
        changes = []
        for role in ROLES:
            prompt = role_prompt(role)
            description = flow_description(prompt, model, num_ctx)
            flow = existing.get(prompt.endpoint)
            if flow and flow.get("description") == description:
                continue
            data = build_graph(template, ollama, prompt.text, model=model, ollama_url=ollama_url, num_ctx=num_ctx)
            if flow:
                self._json("PATCH", f"/api/v1/flows/{flow['id']}", json={"description": description, "data": data})
                changes.append(f"updated {prompt.endpoint}")
            else:
                self._json("POST", "/api/v1/flows/", json={"name": f"SDLC {role}", "endpoint_name": prompt.endpoint,
                                                           "description": description, "data": data})
                changes.append(f"created {prompt.endpoint}")
        return changes

    def flow_drift(self, model: str, num_ctx: int) -> list[str]:
        """Roles whose flow is missing or no longer matches this repository's prompt and model."""
        existing = {flow.get("endpoint_name"): flow for flow in self.list_flows()}
        drift = []
        for role in ROLES:
            prompt = role_prompt(role)
            flow = existing.get(prompt.endpoint)
            if flow is None:
                drift.append(f"{prompt.endpoint} missing")
            elif flow.get("description") != flow_description(prompt, model, num_ctx):
                drift.append(f"{prompt.endpoint} differs from langflow/prompts")
        return drift


def flow_description(prompt: RolePrompt, model: str, num_ctx: int) -> str:
    return (f"AI-native SDLC {prompt.role} role. prompt {prompt.version}, model {model}, num_ctx {num_ctx}, "
            f"timeout {get_settings().ollama_timeout_seconds}s. Managed by sdlc up.")


def build_graph(template: dict, ollama_component: dict, prompt: str, *, model: str, ollama_url: str,
                num_ctx: int) -> dict:
    """Basic Prompting with its language model replaced by Ollama and the system prompt set to `prompt`."""
    graph = copy.deepcopy(template["data"])
    nodes = [node for node in graph["nodes"] if "type" in node.get("data", {})]
    graph["nodes"] = nodes
    model_node = next(node for node in nodes if node["data"]["type"] == "LanguageModelComponent")
    old_id, new_id = model_node["id"], "OllamaModel-SDLC"
    model_node["id"] = model_node["data"]["id"] = new_id
    model_node["data"]["type"] = "OllamaModel"
    model_node["data"]["node"] = copy.deepcopy(ollama_component)
    fields = model_node["data"]["node"]["template"]
    fields["base_url"]["value"] = ollama_url
    fields["model_name"]["value"] = model
    fields["temperature"]["value"] = 0.0
    fields["stream"]["value"] = False
    fields["num_ctx"]["value"] = num_ctx
    fields["timeout"]["value"] = get_settings().ollama_timeout_seconds
    for edge in graph["edges"]:
        for key in ("source", "target", "sourceHandle", "targetHandle", "id"):
            if isinstance(edge.get(key), str):
                edge[key] = edge[key].replace(old_id, new_id).replace("LanguageModelComponent", "OllamaModel")
        for handle in edge.get("data", {}).values():
            if isinstance(handle, dict):
                if handle.get("id") == old_id:
                    handle["id"] = new_id
                if handle.get("dataType") == "LanguageModelComponent":
                    handle["dataType"] = "OllamaModel"
    for node in nodes:
        kind, template_fields = node["data"]["type"], node["data"].get("node", {}).get("template", {})
        if kind == "Prompt":
            template_fields["template"]["value"] = prompt
        if kind in ("ChatInput", "ChatOutput") and "should_store_message" in template_fields:
            template_fields["should_store_message"]["value"] = False
    return graph


def client_from_settings(settings: Settings) -> LangflowClient:
    return LangflowClient(settings.langflow_url, settings.langflow_api_key)
