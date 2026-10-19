"""Versioned artifacts: markdown documents and plans, with canonical SHA-256."""

import difflib
import hashlib
import json
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from controller.app.models import Document, Plan

THREAD_DIR = ".sdlc/runs"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def plan_sha256(payload: dict, test_plan: dict) -> str:
    canonical = json.dumps({"plan": payload, "test_plan": test_plan}, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def thread_path(run_id: uuid.UUID, kind: str) -> str:
    return f"{THREAD_DIR}/{str(run_id)[:8]}/{kind}.md"


def latest_document(session: Session, run_id: uuid.UUID, kind: str) -> Document | None:
    return session.scalar(select(Document).where(Document.run_id == run_id, Document.kind == kind)
                          .order_by(Document.version.desc()).limit(1))


def add_document(session: Session, run_id: uuid.UUID, kind: str, content: str, *, author_kind: str,
                 author: str, data: dict | None = None) -> Document:
    version = (session.scalar(select(func.max(Document.version))
                              .where(Document.run_id == run_id, Document.kind == kind)) or 0) + 1
    document = Document(run_id=run_id, kind=kind, version=version, sha256=sha256_text(content),
                        author_kind=author_kind, author=author, content=content, data=data or {},
                        repo_path=thread_path(run_id, kind))
    session.add(document)
    session.flush()
    return document


def latest_plan(session: Session, run_id: uuid.UUID) -> Plan | None:
    return session.scalar(select(Plan).where(Plan.run_id == run_id).order_by(Plan.version.desc()).limit(1))


def add_plan(session: Session, run_id: uuid.UUID, payload: dict, test_plan: dict, *, author_kind: str,
             author: str) -> Plan:
    version = (session.scalar(select(func.max(Plan.version)).where(Plan.run_id == run_id)) or 0) + 1
    plan = Plan(run_id=run_id, version=version, sha256=plan_sha256(payload, test_plan), author_kind=author_kind,
                author=author, payload=payload, test_plan=test_plan)
    session.add(plan)
    session.flush()
    return plan


HEADING = re.compile(r"^#{1,4} +(.+?)\s*$")


def _sections(content: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"(top)": []}
    current = "(top)"
    for line in content.splitlines():
        match = HEADING.match(line)
        if match:
            current = re.sub(r"\s*<a [^>]*></a>", "", match[1]).strip()  # the SRS/SDS anchors are not the title
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return sections


def section_changes(old: str, new: str) -> list[dict]:
    """The headings whose text changed between two versions of a document, with lines added and removed."""
    before, after = _sections(old), _sections(new)
    changes = []
    for heading in [*after, *[h for h in before if h not in after]]:
        a, b = before.get(heading), after.get(heading)
        if a == b:
            continue
        diff = list(difflib.unified_diff(a or [], b or [], lineterm="", n=0))
        added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        changes.append({"heading": heading, "added": added, "removed": removed,
                        "state": "new" if a is None else "removed" if b is None else "changed"})
    return changes


def document_diff(old: str, new: str, old_label: str, new_label: str) -> str:
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), old_label, new_label, lineterm="", n=2))
