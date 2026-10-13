import pytest

from controller.app.model_preflight import verify_flow_graph, verify_ollama_tags


def test_flow_model_binding_rejects_shared_8b():
    flow = {"id": "flow-1", "data": {"nodes": [{"data": {"node": {"template": {
        "model_name": {"value": "qwen3-vl:8b-instruct"},
        "base_url": {"value": "http://host.docker.internal:11435"},
    }}}}]}}
    with pytest.raises(ValueError, match="qwen3:30b"):
        verify_flow_graph(flow, "flow-1")
    flow["data"]["nodes"][0]["data"]["node"]["template"]["model_name"]["value"] = "qwen3:30b"
    verify_flow_graph(flow, "flow-1")
    with pytest.raises(ValueError, match="qwen3:14b"):
        verify_flow_graph(flow, "flow-1", "qwen3:14b")
    flow["data"]["nodes"][0]["data"]["node"]["template"]["model_name"]["value"] = "qwen3:14b"
    verify_flow_graph(flow, "flow-1", "qwen3:14b")
    flow["data"]["nodes"][0]["data"]["node"]["template"]["model_name"]["value"] = "qwen3:30b-instruct"
    verify_flow_graph(flow, "flow-1", "qwen3:30b-instruct")


def test_ollama_tag_must_match_exact_model():
    with pytest.raises(ValueError, match="unavailable"):
        verify_ollama_tags({"models": [{"name": "qwen3:8b"}]})
    verify_ollama_tags({"models": [{"name": "qwen3:30b"}]})
    verify_ollama_tags({"models": [{"name": "qwen3:14b"}]}, "qwen3:14b")
    verify_ollama_tags({"models": [{"name": "qwen3:30b-instruct"}]}, "qwen3:30b-instruct")
    with pytest.raises(ValueError, match="unavailable"):
        verify_ollama_tags({"models": [{"name": "qwen3:30b"}]}, "qwen3:14b")
