"""A discovery run collects raw requirement sources before anything is extracted."""

import io
import uuid
import zipfile

from fastapi.testclient import TestClient
import pytest

from controller.app.main import app
from controller.tests.integration.test_srs_flow import post
from controller.tests.integration.test_team_loop import drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})
PDF = b"%PDF-1.4\n1 0 obj << /Type /Pages /Kids [3 0 R] /Count 3 >> endobj\n%%EOF\n"


def docx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml'
                         f'/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


def start(sample_repo) -> str:
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    run = post("/api/runs", {"project": "task-board", "discovery": {"title": "Trial", "notes": "sales deck"},
                             "idempotency_key": str(uuid.uuid4())}, status=201)
    assert run["kind"] == "discovery" and run["title"] == "Discovery: Trial"
    drain()
    return run["id"]


def upload(run: str, name: str, data: bytes, status: int = 200):
    response = client.post(f"/api/runs/{run}/sources", params={"filename": name}, content=data,
                           headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == status, response.text
    return response.json()


def test_sources_are_collected_counted_and_kept_for_the_audit(db, sample_repo):
    run = start(sample_repo)
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "INGESTING" and detail["waiting"] == {"role": "product_owner", "action": "ingesting"}

    detail = upload(run, "Sales deck.pdf", PDF)
    deck = detail["sources"][0]
    assert (deck["filename"], deck["kind"], deck["pages"], deck["has_text"]) == ("Sales deck.pdf", "pdf", 3, False)
    assert upload(run, "copy.pdf", PDF, status=409)["detail"] == "copy.pdf has the same content as Sales deck.pdf, already added"
    assert "not accepted" in upload(run, "tool.exe", b"MZ", status=409)["detail"]

    detail = upload(run, "brief.docx", docx("Customers export reports."))
    brief = next(s for s in detail["sources"] if s["kind"] == "docx")
    assert client.get(f"/api/runs/{run}/sources/{brief['id']}/text").json()["text"] == "[p1] Customers export reports.\n"
    detail = post(f"/api/runs/{run}/sources/text", {"title": "Call with sales", "text": "They need SSO."})
    assert [s["filename"] for s in detail["sources"]] == ["Sales deck.pdf", "brief.docx", "Call-with-sales.md"]

    file = client.get(f"/api/runs/{run}/sources/{deck['id']}/file")
    assert file.status_code == 200 and file.content == PDF
    removed = client.post(f"/api/runs/{run}/sources/{deck['id']}/remove", json={"reason": ""})
    assert removed.status_code == 409 and "say why" in removed.json()["detail"]
    detail = post(f"/api/runs/{run}/sources/{deck['id']}/remove", {"reason": "an older version of the deck"})
    assert [s["filename"] for s in detail["sources"]] == ["brief.docx", "Call-with-sales.md"]
    events = [e["type"] for e in client.get(f"/api/runs/{run}/events").json()]
    assert events.count("source.added") == 3 and events.count("source.removed") == 1
    assert client.get(f"/api/runs/{run}/sources/{deck['id']}/file").status_code == 200  # kept on disk


def test_one_open_discovery_per_project(db, sample_repo):
    start(sample_repo)
    response = client.post("/api/runs", json={"project": "task-board", "discovery": {"title": "Again"},
                                              "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and "already has an open discovery" in response.json()["detail"]
