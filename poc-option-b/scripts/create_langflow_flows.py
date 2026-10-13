"""Create three local Option B flows from Langflow 1.12's bundled template."""

import argparse
import copy
import gzip
import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LANGFLOW_URL = "http://127.0.0.1:7860"
OLLAMA_URL = "http://host.docker.internal:11435"
MAX_BYTES = 15_000_000
FLOW_SPECS = (
    ("SDLC Requirements", "sdlc-requirements", "requirements.txt"),
    ("SDLC Planning", "sdlc-planning", "planning.txt"),
    ("SDLC Review", "sdlc-review", "review.txt"),
)


def load_key() -> str:
    path = ROOT / ".env"
    values = dict(
        line.split("=", 1) for line in path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    key = values.get("LANGFLOW_API_KEY", "")
    if not key:
        raise ValueError("LANGFLOW_API_KEY is missing from local .env")
    return key


def request_json(path: str, key: str, payload: dict | None = None, *, method: str | None = None):
    request = Request(
        LANGFLOW_URL + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method=method or ("GET" if payload is None else "POST"),
    )
    with urlopen(request, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Langflow response exceeds size limit")
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if len(body) > MAX_BYTES:
            raise ValueError("Langflow response exceeds size limit")
    return json.loads(body)


def build_graph(example: dict, ollama_component: dict, prompt: str, model: str) -> dict:
    graph = copy.deepcopy(example["data"])
    model_node = next(node for node in graph["nodes"] if node.get("data", {}).get("type") == "LanguageModelComponent")
    old_id = model_node["id"]
    new_id = "OllamaModel-SDLC"
    model_node["id"] = new_id
    model_node["data"]["id"] = new_id
    model_node["data"]["type"] = "OllamaModel"
    model_node["data"]["node"] = copy.deepcopy(ollama_component)
    model_node["data"]["node"]["template"]["base_url"]["value"] = OLLAMA_URL
    model_node["data"]["node"]["template"]["model_name"]["value"] = model
    model_node["data"]["node"]["template"]["temperature"]["value"] = 0.0
    model_node["data"]["node"]["template"]["stream"]["value"] = False
    for edge in graph["edges"]:
        for field in ("source", "target", "sourceHandle", "targetHandle", "id"):
            if isinstance(edge.get(field), str):
                edge[field] = edge[field].replace(old_id, new_id).replace("LanguageModelComponent", "OllamaModel")
        for handle in edge.get("data", {}).values():
            if not isinstance(handle, dict):
                continue
            if handle.get("id") == old_id:
                handle["id"] = new_id
            if handle.get("dataType") == "LanguageModelComponent":
                handle["dataType"] = "OllamaModel"
    for node in graph["nodes"]:
        kind = node.get("data", {}).get("type")
        template = node.get("data", {}).get("node", {}).get("template", {})
        if kind == "Prompt":
            template["template"]["value"] = prompt
        if kind in {"ChatInput", "ChatOutput"}:
            template["should_store_message"]["value"] = False
    return graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("qwen3:14b", "qwen3:30b", "qwen3:30b-instruct"), default="qwen3:30b")
    parser.add_argument("--apply", action="store_true", help="create flows in the local Langflow database")
    args = parser.parse_args()
    key = load_key()
    examples = request_json("/api/v1/flows/basic_examples/", key)
    example = next(item for item in examples if item.get("name") == "Basic Prompting")
    catalog = request_json("/api/v1/all", key)
    ollama = catalog["ollama"]["ext:ollama:ChatOllamaComponent@official"]
    current = request_json("/api/v1/flows/", key)
    existing = {flow["name"]: flow for flow in current}
    output_dir = ROOT / "langflow" / "flows"
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, endpoint, prompt_file in FLOW_SPECS:
        prompt = (ROOT / "langflow" / "prompts" / prompt_file).read_text()
        payload = {
            "name": name,
            "description": f"Option B advisory {endpoint.removeprefix('sdlc-')} flow",
            "endpoint_name": endpoint,
            "data": build_graph(example, ollama, prompt, args.model),
        }
        export_path = output_dir / f"{endpoint}.json"
        if args.apply:
            if name in existing:
                flow = existing[name]
                if flow.get("endpoint_name") != endpoint:
                    raise ValueError(f"existing flow {name} has a different endpoint")
                saved = request_json(
                    f"/api/v1/flows/{flow['id']}", key,
                    {"description": payload["description"], "data": payload["data"]},
                    method="PATCH",
                )
                print(f"updated {name}: {saved['id']}")
            else:
                saved = request_json("/api/v1/flows/", key, payload)
                print(f"created {name}: {saved['id']}")
            payload["id"] = saved["id"]
        else:
            print(f"prepared {name}")
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        if key in serialized:
            raise ValueError("refusing to export a flow containing the API key")
        export_path.write_text(serialized + "\n")


if __name__ == "__main__":
    main()
