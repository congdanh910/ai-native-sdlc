import json
import gzip
from pathlib import Path

import pytest

from controller.app.integrations.langflow import LangflowClient, LangflowInvalidOutput, _http_post
from controller.app.schemas import AnalysisResult, PlanPayload, ReviewResult


@pytest.mark.parametrize("name", ["requirements", "planning", "review"])
def test_saved_prompts_have_no_unbound_template_fields(name: str) -> None:
    prompt = (Path(__file__).resolve().parents[2] / "langflow/prompts" / f"{name}.txt").read_text()
    assert prompt.format() == prompt


def envelope(payload: dict) -> bytes:
    return json.dumps({
        "outputs": [{"outputs": [{"results": {"message": {"text": json.dumps(payload)}}}]}]
    }).encode()


def test_langflow_analysis_and_request_contract() -> None:
    captured = {}

    def transport(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return envelope({
            "summary": "Add task filters", "acceptance_criteria": ["Search works"],
            "questions": [], "assumptions": [],
        })

    client = LangflowClient("http://127.0.0.1:7860", "secret", transport=transport)
    result = client.run_json_flow(
        "requirements-flow", {"requirement": "Add filters"}, AnalysisResult,
        session_id="run-123",
    )
    assert result.summary == "Add task filters"
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:7860/api/v1/run/requirements-flow"
    assert request.get_header("X-api-key") == "secret"
    body = json.loads(request.data)
    assert body["session_id"] == "run-123"
    assert json.loads(body["input_value"]) == {"requirement": "Add filters"}
    assert captured["timeout"] == 120.0


def test_langflow_plan_and_review_are_schema_checked() -> None:
    plan = {"tasks": [{
        "id": "task_1", "description": "Implement", "allowed_paths": ["src"],
        "acceptance_criteria": ["Works"], "dependencies": [],
    }]}
    client = LangflowClient("http://localhost:7860", "secret", transport=lambda _r, _t: envelope(plan))
    assert client.run_json_flow("planning", {}, PlanPayload, session_id="run-1").tasks[0].id == "task_1"

    client = LangflowClient("http://localhost:7860", "secret", transport=lambda _r, _t: envelope({
        "verdict": "needs_changes", "findings": [
            {"severity": "high", "path": "src/app.js", "message": "Missing validation"},
        ],
    }))
    assert client.run_json_flow("review", {}, ReviewResult, session_id="run-1").findings[0].severity == "high"


@pytest.mark.parametrize("body", [
    b"not-json",
    json.dumps({"outputs": []}).encode(),
    envelope({"tasks": [{"id": "bad", "description": "Bad", "allowed_paths": ["../secret"],
                         "acceptance_criteria": ["No"]}]}),
    json.dumps({"outputs": [{"outputs": [{"results": {"message": {"text": "```json\n{}\n```"}}}]}]}).encode(),
])
def test_langflow_invalid_output_is_rejected(body: bytes) -> None:
    client = LangflowClient("http://127.0.0.1:7860", "secret", transport=lambda _r, _t: body)
    with pytest.raises(LangflowInvalidOutput):
        client.run_json_flow("planning", {}, PlanPayload, session_id="run-1")


def test_langflow_rejects_remote_url_and_flow_path() -> None:
    with pytest.raises(ValueError):
        LangflowClient("https://example.com", "secret")
    client = LangflowClient("http://127.0.0.1:7860", "secret", transport=lambda _r, _t: b"{}")
    with pytest.raises(ValueError):
        client.run_json_flow("../../other", {}, PlanPayload, session_id="run-1")


def test_http_transport_decodes_gzip(monkeypatch) -> None:
    class Response:
        headers = {"Content-Encoding": "gzip"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return gzip.compress(b'{"outputs": []}')

    monkeypatch.setattr("controller.app.integrations.langflow.urlopen", lambda _request, timeout: Response())
    assert _http_post(None, 1.0) == b'{"outputs": []}'
