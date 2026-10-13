"""Test real Langflow HTTP transport with an ephemeral echo flow, no model call."""

import copy
from uuid import uuid4
from urllib.request import Request, urlopen

from controller.app.config import get_settings
from controller.app.integrations.langflow import client_from_settings
from controller.app.schemas import AnalysisResult
from scripts.create_langflow_flows import LANGFLOW_URL, load_key, request_json


def echo_graph(example: dict) -> dict:
    graph = copy.deepcopy(example["data"])
    input_node = next(node for node in graph["nodes"] if node.get("data", {}).get("type") == "ChatInput")
    output_node = next(node for node in graph["nodes"] if node.get("data", {}).get("type") == "ChatOutput")
    input_edge = next(edge for edge in graph["edges"] if edge["source"] == input_node["id"])
    output_edge = next(edge for edge in graph["edges"] if edge["target"] == output_node["id"])
    edge = copy.deepcopy(input_edge)
    edge["target"] = output_node["id"]
    edge["targetHandle"] = output_edge["targetHandle"]
    edge["data"]["targetHandle"] = output_edge["data"]["targetHandle"]
    edge["id"] = f"sdlc-echo-{uuid4()}"
    for node in (input_node, output_node):
        node["data"]["node"]["template"]["should_store_message"]["value"] = False
    graph["nodes"] = [input_node, output_node]
    graph["edges"] = [edge]
    return graph


def main() -> None:
    key = load_key()
    examples = request_json("/api/v1/flows/basic_examples/", key)
    example = next(item for item in examples if item.get("name") == "Basic Prompting")
    flow = request_json("/api/v1/flows/", key, {
        "name": f"SDLC Transport Smoke {uuid4().hex[:8]}",
        "description": "Temporary echo flow for HTTP adapter verification; no model",
        "data": echo_graph(example),
    })
    try:
        result = client_from_settings(get_settings()).run_json_flow(
            flow["id"],
            {"summary": "Echo works", "acceptance_criteria": ["JSON returned"],
             "questions": [], "assumptions": []},
            AnalysisResult,
            session_id=f"transport-smoke-{uuid4()}",
        )
        if result.summary != "Echo works":
            raise AssertionError("Langflow echo returned unexpected content")
        print("Langflow HTTP transport, authentication, gzip, and schema validation passed")
    finally:
        request = Request(
            f"{LANGFLOW_URL}/api/v1/flows/{flow['id']}",
            headers={"x-api-key": key}, method="DELETE",
        )
        with urlopen(request, timeout=20):
            pass


if __name__ == "__main__":
    main()
