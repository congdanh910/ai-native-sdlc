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


def test_extraction_writes_an_inventory_whose_items_cite_their_sources(db, sample_repo):
    run = start(sample_repo)
    assert client.post(f"/api/runs/{run}/extract").json()["detail"] == "add at least one source first"
    upload(run, "deck.pdf", PDF)
    post(f"/api/runs/{run}/sources/text", {"title": "Workshop notes",
                                           "text": "## Reports\n- Customers export reports\n- Which formats?"})
    detail = post(f"/api/runs/{run}/extract", {})
    assert detail["status"] == "EXTRACTING"
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "CLARIFYING" and "requirement inventory v1" in detail["reason"]
    inventory = next(d for d in detail["documents"] if d["kind"] == "inventory")
    data = inventory["inventory"]
    names = {s["id"]: s["filename"] for s in detail["sources"]}
    assert [(i["id"], i["type"], i["name"]) for i in data["items"]] == [
        ("REQ-001", "goal", "Goals of deck.pdf"), ("REQ-002", "feature", "Workshop notes"), ("REQ-003", "feature", "Reports"),
        ("REQ-004", "function", "Customers export reports"), ("REQ-005", "function", "Which formats?")]
    assert all(i["sources"] and i["sources"][0]["source"] in names for i in data["items"])
    assert data["problems"] == [] and data["gaps"][0]["question"] == "Which formats?"
    assert [p["covers"] for p in data["parts"]] == ["pages 1-3 of 3", "lines 1-5 of 5"]
    assert "| REQ-004 | Customers export reports |" in inventory["content"] and inventory["commit_sha"]
    detail = post(f"/api/runs/{run}/extract", {})  # a second round from CLARIFYING
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    assert [d["version"] for d in detail["documents"] if d["kind"] == "inventory"] == [1, 2]
