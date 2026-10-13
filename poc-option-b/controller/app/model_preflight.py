"""Read-only checks that bind Langflow flows to the requested Ollama model."""

import json
import zlib
from urllib.request import Request, urlopen

from controller.app.integrations.langflow import FLOW_ID_PATTERN, LangflowClient


EXPECTED_MODEL = "qwen3:30b"
PILOT_MODELS = frozenset({"qwen3:14b", "qwen3:30b", "qwen3:30b-instruct"})
EXPECTED_OLLAMA_CONTAINER_URL = "http://host.docker.internal:11435"
OLLAMA_HOST_URL = "http://127.0.0.1:11435"
MAX_PREFLIGHT_BYTES = 2_000_000


def read_json(url: str, *, api_key: str | None = None) -> dict:
    headers = {"Accept-Encoding": "identity"}
    if api_key:
        headers["x-api-key"] = api_key
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:
        body = response.read(MAX_PREFLIGHT_BYTES + 1)
        encoding = response.headers.get("Content-Encoding", "").lower()
    if len(body) > MAX_PREFLIGHT_BYTES:
        raise ValueError("preflight response is too large")
    if encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        body = decompressor.decompress(body, MAX_PREFLIGHT_BYTES + 1)
        if not decompressor.eof or decompressor.unused_data:
            raise ValueError("preflight gzip response is incomplete")
    elif encoding not in {"", "identity"}:
        raise ValueError("unsupported preflight response encoding")
    if len(body) > MAX_PREFLIGHT_BYTES:
        raise ValueError("preflight response is too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("preflight response is not an object")
    return value


def verify_flow_graph(flow: dict, flow_id: str, model: str = EXPECTED_MODEL) -> None:
    if model not in PILOT_MODELS:
        raise ValueError("unsupported pilot model")
    if flow.get("id") != flow_id:
        raise ValueError("Langflow returned a different flow")
    nodes = flow.get("data", {}).get("nodes", [])
    models = []
    for node in nodes:
        template = node.get("data", {}).get("node", {}).get("template", {})
        if "model_name" in template:
            models.append((
                template["model_name"].get("value"),
                template.get("base_url", {}).get("value"),
            ))
    if models != [(model, EXPECTED_OLLAMA_CONTAINER_URL)]:
        raise ValueError(f"Langflow flow does not target {model} on the configured tunnel endpoint")


def verify_ollama_tags(tags: dict, model: str = EXPECTED_MODEL) -> None:
    if model not in PILOT_MODELS:
        raise ValueError("unsupported pilot model")
    names = {item.get("name") for item in tags.get("models", []) if isinstance(item, dict)}
    if model not in names:
        raise ValueError(f"requested {model} model is unavailable on the tunnel endpoint")


def verify_model_binding(
    client: LangflowClient, flow_ids: list[str], *, model: str = EXPECTED_MODEL,
) -> None:
    if model not in PILOT_MODELS:
        raise ValueError("unsupported pilot model")
    for flow_id in flow_ids:
        if not FLOW_ID_PATTERN.fullmatch(flow_id):
            raise ValueError("invalid Langflow flow ID")
        flow = read_json(f"{client.base_url}/api/v1/flows/{flow_id}", api_key=client.api_key)
        verify_flow_graph(flow, flow_id, model)
    verify_ollama_tags(read_json(f"{OLLAMA_HOST_URL}/api/tags"), model)
