"""The SDS in four passes, diagrams drawn from data, and the depth check of the SDS gate."""

import json

import pytest

from controller.app.project_docs import (Message, ObjectStates, Sds, Table, check_sds_depth, mermaid_erd,
                                         mermaid_flow, mermaid_sequence, mermaid_states, render_sds, srs_features)
from controller.tests.unit.test_langflow import context

SRS = """# Shop - Software Requirements Specification (SRS)

## 2. Conceptual Domain Model

### 2.4 Domain Object State

#### 2.4.1 Order State

## 6. Business Flows

### 6.1 Checkout

### 6.2 Refund

## 7. Features and User Stories

### 7.1 Orders (OR)

#### 7.1.1 OR-US-01: Place an order (BUYER)
"""


def test_diagrams_parse_whatever_the_labels_hold():
    flow = "\n".join(mermaid_flow([], [('Nhân sự "A"', "VMS [core]", "gửi; ảnh")]))
    assert 'n0["Nhân sự  A"]' in flow and 'n0 -- "gửi  ảnh" --> n1' in flow
    sequence = "\n".join(mermaid_sequence(["Camera"], [Message(source="Camera", target="VMS", text="event: face#1")]))
    assert "participant P0 as Camera" in sequence and "P0->>P1: event: face 1" in sequence
    states = "\n".join(mermaid_states(ObjectStates.model_validate(
        {"object": "Order", "states": [{"term": "New", "meaning": ""}, {"term": "Paid", "meaning": ""}],
         "transitions": [{"source": "New", "target": "Paid", "trigger": "pay"}, {"source": "Paid", "target": "Refunded"}]})))
    assert "[*] --> S0" in states and "S0 --> S1 : pay" in states and 'state "Refunded" as S2' in states
    erd = "\n".join(mermaid_erd([Table.model_validate({"name": "face-template", "fields": [
        {"name": "created at", "type": "timestamp with tz"}]})]))
    assert "face_template {" in erd and "timestamp_with_tz created_at" in erd


def full_sds(**overrides) -> Sds:
    data = {
        "purpose": "p", "scope": "s", "traceability": [{"object": "Order", "table": "orders"}], "context": "c",
        "actors": ["Buyer"], "external_systems": [{"name": "Payments", "purpose": "charge"}],
        "components": [{"name": "API", "responsibility": "r", "calls": ["Payments"]}],
        "stack": [{"term": "Backend", "meaning": "Node.js 20"}],
        "states": [{"object": "Order", "states": [{"term": "New", "meaning": "placed"}]}],
        "tables": [{"name": "orders", "fields": [{"name": "id", "type": "uuid", "required": True}]}],
        "process": [{"name": n, "participants": ["Buyer", "API"], "messages": [{"source": "Buyer", "target": "API", "text": "x"}]}
                    for n in ("Checkout", "Refund")],
        "dtos": [{"name": "OrderResponse", "object": "Order", "fields": [{"name": "id", "type": "string"}]}],
        "endpoints": [{"method": "POST", "path": "/api/v1/orders", "feature": "OR", "stories": ["OR-US-01"],
                       "request": '{"items": []}', "response": '{"id": "o-1"}'}],
        "auth": {"method": "session", "flow": [{"source": "Buyer", "target": "API", "text": "sign in"}]},
        "zones": [{"name": "AWS account", "kind": "cloud"}, {"name": "Private subnet", "kind": "subnet", "parent": "AWS account"}],
        "deployment": [{"name": "API service", "runs": "API", "zone": "Private subnet", "service": "Amazon ECS on Fargate",
                        "scaling": "2-6 tasks"}],
        "links": [{"source": "Buyer", "target": "API service", "protocol": "HTTPS 443", "security": "TLS"}],
        "environments": [{"term": "production", "meaning": "two availability zones"}],
        "resilience": [{"term": "Backup", "meaning": "daily snapshots, RPO 24 h"}],
        "decisions": [{"title": "t", "context": "c", "decision": "d"}]}
    data.update(overrides)
    return Sds.model_validate(data)


def test_the_depth_check_wants_the_diagrams_dtos_and_apis():
    features = srs_features(SRS)
    content = render_sds(full_sds(), features, product="Shop", author="t")
    assert check_sds_depth(content, SRS) == []
    assert "#### 2.4.1 Order State" in content and "### 4.4 Process View" in content and "erDiagram" in content
    physical = content.split("### 4.5 Physical View")[1].split("### 4.6")[0]
    assert 'subgraph z0["AWS account"]' in physical and 'subgraph z1["Private subnet"]' in physical
    assert '"HTTPS 443 · TLS"' in physical and "| API service | Private subnet | Amazon ECS on Fargate |" in physical
    assert "| production | two availability zones |" in physical and "| Backup | daily snapshots, RPO 24 h |" in physical
    thin = render_sds(full_sds(process=[], dtos=[], states=[], auth={}, zones=[], links=[]), features, product="Shop",
                      author="t")
    assert check_sds_depth(thin, SRS) == [
        "§4.5 has no deployment diagram (zones, nodes and their connections)",
        "§4.4 has 0 sequence diagram(s) for 2 SRS business flow(s)",
        "§2.4 has 0 state diagram(s) for 1 SRS entities with a lifecycle",
        "§6.2 has no DTOs for the core objects", "§7.1 has no sign-in flow"]


def test_the_architect_writes_the_sds_in_four_passes_each_reading_the_earlier_ones(settings, tmp_path, monkeypatch):
    from controller.app.team.langflow_team import LangflowTeam
    answers = {
        "sds-model": {"purpose": "p", "scope": "s", "traceability": [{"object": "Order"}],
                      "states": [{"object": "Order", "states": ["New", {"term": "Paid", "meaning": "paid"}]}]},
        "sds-architecture": {"context": "c", "components": [{"name": "API", "responsibility": "r", "calls": "Payments"}],
                             "stack": [{"term": "Backend", "meaning": "Node"}],
                             "process": [{"name": "Checkout", "messages": ["Buyer -> API: place order"]}],
                             "backend": {"structure": ["src/", "src/orders/"], "layers": [{"term": "Service", "meaning": "rules"}]}},
        "sds-interfaces": {"endpoints": [{"method": "GET", "path": "/orders", "feature": "OR", "response": {"items": []}}],
                           "dtos": [{"name": "OrderDto", "fields": ["id: uuid"]}]},
        "sds-operations": {"decisions": [{"title": "t", "context": "c", "decision": "d"}],
                           "metrics": [{"term": "Orders per day", "meaning": "count"}]}}
    seen = []

    class Client:
        def run(self, endpoint, payload, session_id):
            part = endpoint.removeprefix("sdlc-v3-")
            seen.append((part, sorted(payload["design_so_far"])))
            if answers[part] is None:
                raise TimeoutError("codex timed out after 600s")
            return json.dumps(answers[part])
    team = LangflowTeam(settings, client=Client(), check_capacity=lambda: [], check_ollama=lambda: None)
    sds = team.write_sds(context())
    assert [p for p, _ in seen] == ["sds-model", "sds-architecture", "sds-interfaces", "sds-operations"]
    assert "states" in seen[1][1] and "endpoints" in seen[3][1]  # later passes see what earlier ones wrote
    assert sds.process[0].messages[0].text == "place order" and sds.dtos[0].fields[0].type == "uuid"
    assert sds.backend.structure == "src/\nsrc/orders/" and sds.endpoints[0].response.startswith("{")
    assert sds.metrics[0].name == "Orders per day" and team.last_call.attempts == 4

    # A retry of the job (the Mac slept during part 3, say) reuses the parts already written; a new log starts clean.
    from controller.app import joblog
    monkeypatch.setattr(joblog, "DATA_DIR", tmp_path)
    seen.clear()
    answers["sds-interfaces"], interfaces = None, answers["sds-interfaces"]
    joblog.start(context().run_id or "run", "job-1", "specify")
    try:
        with pytest.raises(TimeoutError):
            team.write_sds(context())
        assert [p for p, _ in seen] == ["sds-model", "sds-architecture", "sds-interfaces"]
        seen.clear()
        answers["sds-interfaces"] = interfaces
        again = team.write_sds(context())
        assert [p for p, _ in seen] == ["sds-interfaces", "sds-operations"]  # parts 1 and 2 came from the first try
        assert again.process[0].messages[0].text == "place order" and team.last_call.attempts == 2
        assert "sds sds-model: reused the result of an earlier attempt" in joblog.current().read_text()
    finally:
        joblog.stop()


def test_options_compare_hosting_variants_on_the_projects_platforms():
    from controller.app.project_docs import Options, check_options, number_options, render_options
    from controller.app.team.normalize import options as normalize_options
    data = normalize_options({"context": "c", "recommendation": "Central", "reason": "r", "options": [
        {"name": "Central", "summary": "s", "recommended_hosting": "On-Premise",
         "hosting": [{"platform": "Amazon Web Services", "name": "AWS managed", "services": {"API": "Amazon ECS"}},
                     {"platform": "On-Premise", "name": "Own data centre", "services": ["API: VM with Docker"],
                      "pros": "- data stays in the country"}]},
        {"name": "Edge", "summary": "s"}]})
    options = number_options(Options.model_validate(data))
    central = options.options[0]
    assert [(v.id, v.platform) for v in central.hosting] == [("PA1-H1", "aws"), ("PA1-H2", "on_premise")]
    assert central.recommended_hosting == "PA1-H2" and central.hosting[1].services[0].service == "VM with Docker"
    assert check_options(options, None, ["aws", "on_premise"]) == ["PA2 has no hosting variant"]
    assert check_options(options, None, ["on_premise"])[0] == "PA1 hosting outside architecture.hosting_platforms: PA1-H1 (aws)"
    text = render_options(options, title="Shop")
    assert "| Platform | AWS | On-premise |" in text and "**Recommended hosting for PA1:** PA1-H2 Own data centre." in text
    assert "| API | VM with Docker | — |" in text
