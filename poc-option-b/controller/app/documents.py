"""Versioned artifacts: markdown documents and plans, with canonical SHA-256."""

import hashlib
import json
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
