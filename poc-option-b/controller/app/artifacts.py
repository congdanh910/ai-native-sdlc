"""Persist small JSON evidence files with a checksum and atomic rename."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.models import Artifact
from controller.app.repo_context import RepoContext


MAX_ARTIFACT_BYTES = 1_000_000


def record_agent_context(session: Session, run_id: UUID, context: RepoContext) -> Artifact:
    existing = session.scalar(
        select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "agent_context").limit(1)
    )
    if existing is not None:
        if existing.checksum != context.sha256 or existing.storage_path != context.artifact_path:
            raise ValueError("pinned agent context changed within this run")
        return existing
    artifact = Artifact(
        id=uuid4(), run_id=run_id, kind="agent_context",
        storage_path=context.artifact_path, checksum=context.sha256,
    )
    session.add(artifact)
    return artifact


def save_json_artifact(
    session: Session, root: Path, run_id: UUID, kind: str, payload: dict,
    task_id: UUID | None = None,
) -> Artifact:
    if not kind.isidentifier():
        raise ValueError("invalid artifact kind")
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds size limit")
    directory = root.resolve() / str(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("artifact directory is a symlink")
    final_path = directory / f"{kind}-{uuid4().hex}.json"
    descriptor, temporary_path = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, final_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    artifact = Artifact(
        id=uuid4(), run_id=run_id, task_id=task_id, kind=kind,
        storage_path=str(final_path), checksum=hashlib.sha256(data).hexdigest(),
    )
    session.add(artifact)
    return artifact
