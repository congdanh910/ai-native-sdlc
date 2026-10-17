import httpx

from controller.app import capacity, health


def test_overall_takes_worst_status():
    ok = health.Check("a", health.OK, "")
    warn = health.Check("b", health.WARN, "")
    fail = health.Check("c", health.FAIL, "")
    assert health.overall([ok]) == health.OK
    assert health.overall([ok, warn]) == health.WARN
    assert health.overall([warn, fail]) == health.FAIL


def test_env_file_placeholders(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# CHANGE_ME in a comment\nA=1\nB=CHANGE_ME_SECRET\n")
    assert health.env_file_placeholders(env) == ["B"]


def test_check_ollama_requires_configured_model(settings, monkeypatch):
    body = {"models": [{"name": "qwen3:14b"}]}
    monkeypatch.setattr(health.httpx, "get", lambda *a, **k: httpx.Response(200, json=body, request=httpx.Request("GET", "http://x")))
    assert health.check_ollama(settings).status == health.FAIL
    body["models"].append({"name": "qwen3:30b-instruct"})
    assert health.check_ollama(settings).status == health.OK


def test_check_ollama_reports_connection_errors(settings, monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(health.httpx, "get", refuse)
    check = health.check_ollama(settings)
    assert check.status == health.FAIL and "refused" in check.detail


def test_capacity_problems_are_warnings(settings, monkeypatch):
    monkeypatch.setattr(capacity, "sample", lambda s: capacity.CapacitySample(1000, 95))
    assert health.check_gx10_capacity(settings).status == health.WARN

    def unreachable(s):
        raise RuntimeError("SSH read failed")
    monkeypatch.setattr(capacity, "sample", unreachable)
    assert health.check_gx10_capacity(settings).status == health.WARN


def test_errors_are_shortened_to_one_line():
    class Wrapped(Exception):
        orig = ConnectionRefusedError("connection refused\nlong details")
    check = health._guard("postgres", "Run ./sdlc up", lambda: (_ for _ in ()).throw(Wrapped("noise")))
    assert check.detail == "connection refused"
