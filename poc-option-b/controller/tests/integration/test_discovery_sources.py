"""A discovery run collects raw requirement sources before anything is extracted."""

import io
import json
from pathlib import Path
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
    assert client.post(f"/api/runs/{run}/extract").json()["detail"] == "add at least one requirement source first"
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
    assert client.post(f"/api/runs/{run}/extract").json()["detail"].startswith("nothing new since inventory v1")
    upload(run, "brief.docx", docx("Customers export reports."))  # a new source: a second round reads only it
    detail = post(f"/api/runs/{run}/extract", {})
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    assert [d["version"] for d in detail["documents"] if d["kind"] == "inventory"] == [1, 2]
    second = [d for d in detail["documents"] if d["kind"] == "inventory"][-1]["inventory"]
    assert len(second["parts"]) == 3 and second["parts"][-1]["covers"] == "lines 1-1 of 1"


def test_a_round_of_questions_answers_minutes_and_extract_again(db, sample_repo):
    run = start(sample_repo)
    post(f"/api/runs/{run}/sources/text", {"title": "Brief", "text": "## Reports\n- Customers export reports\n"
                                                                     "- Which formats?\n- Who may export?"})
    post(f"/api/runs/{run}/extract", {})
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    questions = detail["questions"]
    assert [(q["number"], q["question"], q["owner_role"], q["status"]) for q in questions] == [
        ("Q-001", "Which formats?", "po", "open"), ("Q-002", "Who may export?", "po", "open")]
    assert [r["id"] for r in detail["roles"]][:3] == ["po", "ba", "sa"]
    items = {i["name"]: i["id"] for i in next(d for d in detail["documents"] if d["kind"] == "inventory")["inventory"]["items"]}
    assert "Which formats" in client.get(f"/api/runs/{run}/workshop.md", params={"roles": "po"}).text
    cards = [(i["role"], i["action"], i.get("questions")) for i in client.get("/api/inbox").json() if i["id"] == run]
    assert cards == [("po", "answer_questions", 2), ("product_owner", "clarifying", None)]
    assert client.post(f"/api/runs/{run}/extract").json()["detail"].startswith("nothing new since inventory v1")

    q1, q2 = questions[0]["id"], questions[1]["id"]
    detail = post(f"/api/runs/{run}/questions/{q1}/answer", {"answer": "CSV and PDF"})
    assert detail["decisions"][0] | {} == {**detail["decisions"][0], "id": "D-001", "basis": "answer", "status": "accepted",
                                           "question": "Q-001", "items": [items["Which formats?"]]}
    missing = client.post(f"/api/runs/{run}/questions/{q2}/status", json={"status": "deferred", "reason": ""})
    assert missing.status_code == 422 or "say why" in missing.json()["detail"]
    post(f"/api/runs/{run}/questions/{q2}/reassign", {"role": "dpo", "reason": "export of personal data"})
    assert client.post(f"/api/runs/{run}/questions/{q2}/reassign", json={"role": "cfo"}).status_code == 409
    detail = client.get(f"/api/runs/{run}").json()
    assert [d["kind"] for d in detail["documents"]].count("decisions") == 1  # a new version comes with the next round

    post(f"/api/runs/{run}/sources/text", {"title": "Workshop 17-10", "purpose": "minutes",
                                           "text": f"- Decision: only managers export (Q-002) {items['Customers export reports']}"})
    detail = post(f"/api/runs/{run}/extract", {})
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "CLARIFYING" and "1 decision(s) from minutes to accept" in detail["reason"]
    inventory = [d for d in detail["documents"] if d["kind"] == "inventory"][-1]["inventory"]
    assert {i["name"]: i["id"] for i in inventory["items"]} == items  # ids stay across rounds
    decisions_md = [d for d in detail["documents"] if d["kind"] == "decisions"][-1]["content"]
    assert "| D-001 | CSV and PDF |" in decisions_md and "answer to Q-001" in decisions_md
    assert "chunks" not in inventory and inventory["decisions_applied"] == ["D-001"]
    proposed = detail["decisions"][1]
    assert (proposed["id"], proposed["basis"], proposed["status"], proposed["role"], proposed["question"]) == (
        "D-002", "minutes", "proposed", "dpo", "Q-002")
    post(f"/api/runs/{run}/decisions/2", {"accept": True})
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["questions"][1]["status"] == "answered" and detail["questions"][1]["answer"].startswith("only managers export")

    post(f"/api/runs/{run}/extract", {})  # D-002 is new: the mock confirms the item it names
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    inventory = [d for d in detail["documents"] if d["kind"] == "inventory"][-1]
    export = next(i for i in inventory["inventory"]["items"] if i["name"] == "Customers export reports")
    assert export["status"] == "confirmed" and export["sources"][-1]["source"] == "decision:D-002"
    assert inventory["inventory"]["problems"] == []
    jobs = [e for e in detail["executions"] if e["kind"] == "extract"]
    assert len(jobs) == 3



def test_the_baseline_gate_then_requirements_in_the_repository(db, sample_repo):
    import subprocess
    run = start(sample_repo)
    post(f"/api/runs/{run}/sources/text", {"title": "Brief", "text": "## Reports\n- Customers export reports"})
    post(f"/api/runs/{run}/extract", {})
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    items = {i["name"]: i["id"] for i in [d for d in detail["documents"] if d["kind"] == "inventory"][-1]["inventory"]["items"]}
    assert "state the scope" in client.post(f"/api/runs/{run}/baseline", json={"scope": " "}).json()["detail"]
    detail = post(f"/api/runs/{run}/baseline", {"scope": "Phase 1: reports"})
    assert detail["status"] == "WAITING_BASELINE_APPROVAL" and detail["checklist"]["gate"] == "baseline"
    failing = {c["id"]: c["detail"] for c in detail["checklist"]["controller"] if not c["ok"]}
    assert failing == {"functions": f"{items['Brief']} Brief has no function"}  # the pasted title became a feature without functions
    baseline = [d for d in detail["documents"] if d["kind"] == "baseline"][-1]
    refused = client.post(f"/api/runs/{run}/baseline/approve", json={"document_id": baseline["id"], "checked": []})
    assert refused.status_code == 409 and "controller checks fail" in refused.json()["detail"]
    post(f"/api/runs/{run}/baseline/reject", {"reason": "the title is not a feature"})

    bad = client.post(f"/api/runs/{run}/decisions", json={"text": "Drop it", "effect": "out_of_scope"})
    assert "name the REQ ids" in bad.json()["detail"]
    post(f"/api/runs/{run}/decisions", {"text": "The pasted title is not a feature", "items": [items["Brief"]],
                                        "effect": "out_of_scope"})  # applied by the controller, whatever the model reads
    post(f"/api/runs/{run}/extract", {})
    drain()
    detail = post(f"/api/runs/{run}/baseline", {"scope": "Phase 1: reports"})
    assert all(c["ok"] for c in detail["checklist"]["controller"]), detail["checklist"]["controller"]
    baseline = [d for d in detail["documents"] if d["kind"] == "baseline"][-1]
    assert "## Out of scope" in baseline["content"] and "| " + items["Brief"] + " | Brief | D-001 |" in baseline["content"]
    missing = client.post(f"/api/runs/{run}/baseline/approve", json={"document_id": baseline["id"], "checked": []})
    assert "tick every review item" in missing.json()["detail"]
    ticks = [h["id"] for h in detail["checklist"]["human"]]
    detail = post(f"/api/runs/{run}/baseline/approve", {"document_id": baseline["id"], "checked": ticks})
    assert detail["status"] == "PR_OPEN"
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "WAITING_PR_APPROVAL"
    files = subprocess.run(["git", "-C", detail_worktree(run), "ls-tree", "-r", "--name-only", "HEAD", "requirements/"],
                           capture_output=True, text=True).stdout.split()
    assert files == ["requirements/baseline.json", "requirements/baseline.md", "requirements/decisions.md",
                     "requirements/inventory.md", "requirements/questions.md", "requirements/sources.md",
                     "requirements/sources/Brief.md"]
    post(f"/api/runs/{run}/pr-decision", {"decision": "approve", "checked": [h["id"] for h in detail["checklist"]["human"]]})
    assert client.get(f"/api/runs/{run}").json()["status"] == "COMPLETED"


def detail_worktree(run: str) -> str:
    from controller.app.db import session_scope
    from controller.app.models import Run
    with session_scope() as session:
        return session.get(Run, uuid.UUID(run)).worktree_path


def merged_baseline(sample_repo) -> dict:
    """A discovery run up to its approved baseline, merged into main of the sample repo as GitHub would."""
    import subprocess
    run = start(sample_repo)
    post(f"/api/runs/{run}/sources/text", {"title": "Reports", "text": "- Customers export reports\n- Admins see every report"})
    post(f"/api/runs/{run}/extract", {})
    drain()
    detail = post(f"/api/runs/{run}/baseline", {"scope": "Phase 1"})
    baseline = [d for d in detail["documents"] if d["kind"] == "baseline"][-1]
    post(f"/api/runs/{run}/baseline/approve", {"document_id": baseline["id"],
                                               "checked": [h["id"] for h in detail["checklist"]["human"]]})
    drain()
    worktree = detail_worktree(run)
    branch = client.get(f"/api/runs/{run}").json()["branch"]
    subprocess.run(["git", "-C", str(sample_repo), "pull", "--quiet", "--no-rebase", "--no-edit", worktree, branch],
                   check=True, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                                    "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    post(f"/api/runs/{run}/pr-decision", {"decision": "approve", "checked": [h["id"] for h in client.get(
        f"/api/runs/{run}").json()["checklist"]["human"]]})
    return baseline


def test_an_srs_run_builds_on_the_merged_baseline_and_traces_every_item(db, sample_repo):
    baseline = merged_baseline(sample_repo)
    run = post("/api/runs", {"project": "task-board", "brief": {"title": "Reports", "from_baseline": True},
                             "idempotency_key": str(uuid.uuid4())}, status=201)
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "WAITING_SPEC_APPROVAL", detail["reason"]
    srs = [d for d in detail["documents"] if d["kind"] == "srs"][-1]["content"]
    assert "## Appendix A. Requirement traceability" in srs
    trace = {c["id"]: c for c in detail["checklist"]["controller"]}["trace"]
    assert trace["ok"], trace
    items = [i["id"] for i in json.loads(open(Path(sample_repo) / "requirements" / "baseline.json").read())["items"]]
    assert items and all(f"| {i} |" in srs for i in items)


def test_an_srs_from_baseline_needs_the_baseline_on_main(db, sample_repo):
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    run = post("/api/runs", {"project": "task-board", "brief": {"title": "Reports", "from_baseline": True},
                             "idempotency_key": str(uuid.uuid4())}, status=201)
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "NEEDS_HUMAN" and "merge the discovery pull request first" in detail["reason"]
    refused = client.post("/api/runs", json={"project": "task-board", "brief": {"title": "Reports"},
                                             "idempotency_key": str(uuid.uuid4())})
    assert refused.status_code == 422


def test_a_codex_business_analyst_reads_pdfs_as_text(db, sample_repo, monkeypatch):
    from controller.app import config as config_module
    from controller.app.discovery import sources as sources_module
    settings = config_module.get_settings().model_copy(update={"sdlc_discovery_engine": "codex-readonly"})
    for module in ("controller.app.services", "controller.app.coordinator"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    monkeypatch.setattr(sources_module, "pdf_text", lambda path, tool, timeout: "[page 1] - Customers export reports\n")
    run = start(sample_repo)
    upload(run, "deck.pdf", PDF)
    post(f"/api/runs/{run}/extract", {})
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    inventory = [d for d in detail["documents"] if d["kind"] == "inventory"][-1]["inventory"]
    assert [p["covers"] for p in inventory["parts"]] == ["lines 1-1 of 1"]
    item = inventory["items"][0]
    assert (item["name"], item["sources"][0]["location"]) == ("Customers export reports", "page 1")
    assert detail["sources"][0]["has_text"]  # the text view is kept next to the PDF
