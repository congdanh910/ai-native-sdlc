from fastapi.testclient import TestClient

from controller.app.main import app


def test_liveness_does_not_require_external_services() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
