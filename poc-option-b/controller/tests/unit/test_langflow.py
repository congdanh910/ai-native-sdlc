import json

import httpx
import pytest

from controller.app.integrations import langflow
from controller.app.integrations.langflow import InvalidModelOutput, LangflowClient, extract_json, role_prompt
from controller.app.projects.config import effective_policy, parse_project_config
from controller.app.config import PROJECT_ROOT
from controller.app.team import normalize
from controller.app.team.base import CapacityBusy, RunContext
from controller.app.team.langflow_team import LangflowTeam


def envelope(text: str) -> dict:
    return {"outputs": [{"outputs": [{"results": {"message": {"text": text}}}]}]}


def test_every_role_prompt_loads_without_braces():
    for role in langflow.ROLES:
        prompt = role_prompt(role)
        assert "untrusted data" in prompt.text and len(prompt.version) == 12
        assert prompt.endpoint == f"sdlc-v3-{role}"


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here it is: {"a": 1} hope this helps') == {"a": 1}
    with pytest.raises(InvalidModelOutput):
        extract_json("no json at all")
    with pytest.raises(InvalidModelOutput):
        extract_json("[1, 2]")


def test_client_parses_one_chat_output_and_sends_api_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["x-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=envelope('{"ok": true}'))
    client = LangflowClient("http://127.0.0.1:7870", "secret", transport=httpx.MockTransport(handler))
    assert client.run("sdlc-v3-spec", {"x": 1}, session_id="s") == '{"ok": true}'
    assert seen["key"] == "secret" and json.loads(seen["body"]["input_value"]) == {"x": 1}


def test_client_refuses_remote_urls_and_reports_http_errors():
    with pytest.raises(ValueError):
        LangflowClient("http://langflow.example.com", "k")
    client = LangflowClient("http://127.0.0.1:7870", "k",
                            transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(langflow.LangflowError, match="HTTP 500"):
        client.run("sdlc-v3-spec", {}, session_id="s")


def test_normalize_repairs_shape_only():
    intent = normalize.intent({"problem": "p", "outcome": ["- a", "b"], "open_questions": ["[blocking] - when?"]}, "T")
    assert intent["outcome"] == "- a\n- b" and intent["open_questions"] == "- [blocking] when?"
    spec = normalize.spec({"acceptance_criteria": ["x", {"text": "y"}], "questions": ["why?"]})
    assert [c["id"] for c in spec["acceptance_criteria"]] == ["AC-1", "AC-2"]
    assert spec["questions"] == [{"question": "why?", "blocking": False}]
    plan = normalize.plan({"tasks": [{"id": "T1", "modify": "src/a.js", "references": ["src/a.js"]}]}, ["unit-tests"])
    assert plan["tasks"][0]["modify"] == ["src/a.js"] and plan["tasks"][0]["validation"] == ["unit-tests"]
    assert plan["tasks"][0]["references"] == [{"file": "src/a.js", "symbol": None}]


class FakeClient:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def run(self, endpoint, payload, *, session_id):
        self.calls.append((endpoint, payload))
        return self.answers.pop(0)


def context() -> RunContext:
    text = (PROJECT_ROOT / "examples/task-board/.sdlc/project.yaml").read_text()
    return RunContext(title="Due dates", policy=effective_policy(parse_project_config(text)), agents_md="a",
                      review_md=None, intent_md="# Intent", files=["src/tasks.js"], spec_md="- AC-1: x",
                      repository={"files": ["src/tasks.js"], "symbols": {}, "selected_files": []})


SPEC = {"goal": "g", "user_stories": ["s"], "acceptance_criteria": [{"id": "AC-1", "text": "x"}]}


def test_invalid_answer_is_sent_back_with_the_error(settings):
    client = FakeClient(["not json", json.dumps({"goal": "g"}), json.dumps(SPEC)])
    team = LangflowTeam(settings, client=client, check_capacity=lambda: [])
    spec = team.specify(context())
    assert spec.acceptance_criteria[0].id == "AC-1"
    assert team.last_call.attempts == 3 and team.last_call.engine == "qwen3:30b-instruct"
    assert "format_feedback" not in client.calls[0][1] and "format_feedback" in client.calls[2][1]


def test_gives_up_after_three_invalid_answers(settings):
    team = LangflowTeam(settings, client=FakeClient(["x", "y", "z"]), check_capacity=lambda: [])
    with pytest.raises(InvalidModelOutput, match="after 3 attempts"):
        team.specify(context())


def test_busy_gx10_stops_before_any_model_call(settings):
    client = FakeClient([json.dumps(SPEC)])
    team = LangflowTeam(settings, client=client, check_capacity=lambda: ["GPU utilization 95% is above 20%"])
    with pytest.raises(CapacityBusy, match="GPU"):
        team.specify(context())
    assert client.calls == []


def test_doing_roles_remain_mocked(settings):
    team = LangflowTeam(settings, client=FakeClient([]), check_capacity=lambda: [])
    assert team.validate(context(), None).detail.startswith("mock:")


def test_normalize_joins_list_sentences_in_test_cases():
    data = normalize.test_plan({"cases": [{"task_id": "T1", "criterion": "AC-1", "description": ["given", "when"],
                                           "expected": ["a", "b"], "target_file": "tests/x.test.js"}]})
    case = data["cases"][0]
    assert case["expected"] == "a; b" and case["description"] == "given; when" and case["id"] == "TC-1"
