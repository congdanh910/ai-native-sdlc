from fastapi.testclient import TestClient

from controller.app import health, main

client = TestClient(main.app)


def test_live():
    assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_returns_503_when_a_check_fails(monkeypatch):
    checks = [health.Check("postgres", health.OK, ""), health.Check("ollama", health.FAIL, "down")]
    monkeypatch.setattr(main.health, "readiness_checks", lambda s, e: checks)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "fail"


def test_ready_returns_200_when_all_pass(monkeypatch):
    monkeypatch.setattr(main.health, "readiness_checks", lambda s, e: [health.Check("x", health.OK, "")])
    assert client.get("/health/ready").status_code == 200


def test_status_page_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "SDLC Control Room" in response.text
