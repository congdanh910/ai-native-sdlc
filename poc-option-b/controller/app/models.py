"""SQLAlchemy models. PostgreSQL is the authoritative state store."""

from datetime import datetime
import uuid

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _id()
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    mirror_path: Mapped[str] = mapped_column(Text, nullable=False)
    readiness: Mapped[str] = mapped_column(String(16), nullable=False, default="not_ready")
    readiness_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    checked_commit: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)


class EvalRun(Base):
    """One execution of an eval suite; its cases are experiment runs."""
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = _id()
    suite: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)  # plan (full arrives with B4)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    results: Mapped[dict | None] = mapped_column(JSONB)
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("eval_runs.id"))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="feature", server_default="feature")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    base_commit: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(255))
    worktree_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_modes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    commit_artifacts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    awaiting_start: Mapped[str | None] = mapped_column(String(16))
    resume_status: Mapped[str | None] = mapped_column(String(32))
    status_reason: Mapped[str | None] = mapped_column(Text)
    experiment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stage_overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    eval_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("eval_runs.id"))
    eval_case: Mapped[str | None] = mapped_column(String(64))
    human_corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failure_category: Mapped[str | None] = mapped_column(String(32))
    failure_stage: Mapped[str | None] = mapped_column(String(32))
    failure_role: Mapped[str | None] = mapped_column(String(32))
    failure_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)
    __table_args__ = (Index("ix_runs_status", "status"), Index("ix_runs_project", "project_id"))


class Document(Base):
    """Versioned markdown artifacts: intent, spec, review, report."""
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    author_kind: Mapped[str] = mapped_column(String(8), nullable=False)  # ai | human
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    repo_path: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("run_id", "kind", "version"),)


class Clarification(Base):
    __tablename__ = "clarifications"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    # Discovery questions only: what kind, who answers (a role id of the project's discovery.roles), where it stands.
    kind: Mapped[str | None] = mapped_column(String(32))
    owner_role: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(16))  # open / answered / deferred / withdrawn
    why: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list | None] = mapped_column(JSONB)  # REQ ids the question is about
    citations: Mapped[list | None] = mapped_column(JSONB)  # [{source, location}]
    round: Mapped[int | None] = mapped_column(Integer)  # the extraction round that raised it
    position: Mapped[int | None] = mapped_column(Integer)  # Q-001 ... in the order the questions were raised
    answered_by: Mapped[str | None] = mapped_column(String(64))
    status_reason: Mapped[str | None] = mapped_column(Text)  # why it was deferred, withdrawn or reassigned


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    author_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    test_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    grounding_report: Mapped[dict | None] = mapped_column(JSONB)
    regenerated_from: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("run_id", "version"),)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    external_task_id: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (UniqueConstraint("run_id", "plan_id", "external_task_id"),)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(8), nullable=False)  # spec | plan | pr
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    acting_role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    checklist: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # what was checked at the gate
    created_at: Mapped[datetime] = _created()


class Execution(Base):
    """One unit of work by a team role; doubles as the job queue entry."""
    __tablename__ = "executions"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    side_effects: Mapped[bool] = mapped_column(Boolean, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    engine: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_executions_status", "status"),)


class PullRequest(Base):
    __tablename__ = "pull_requests"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), unique=True, nullable=False)
    number: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    head_sha: Mapped[str | None] = mapped_column(String(40))
    ci_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    merged_sha: Mapped[str | None] = mapped_column(String(40))
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created()


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    acting_role: Mapped[str | None] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_events_run", "run_id"),)


class Artifact(Base):
    """Checksummed snapshots that are not markdown documents (project config, AGENTS.md)."""
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created()


class Source(Base):
    """A raw requirement file of a discovery run, stored once per content hash."""
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf, image, docx, xlsx, pptx, text
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pages: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    text_path: Mapped[str | None] = mapped_column(Text)  # Markdown with locations, for docx/xlsx/pptx/text
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # kept for the audit trail
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="requirements")  # or "minutes"
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("run_id", "sha256", name="uq_sources_run_sha"),)


class Decision(Base):
    """A discovery decision: an answer to a question, or what minutes of a meeting decided. Decisions the Business
    Analyst reads from minutes are proposed until the role that owns them accepts them."""
    __tablename__ = "decisions"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # D-001 ...
    text: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)  # answer / minutes
    question_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clarifications.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"))
    location: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # who owns it
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # proposed / accepted / rejected
    proposed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("run_id", "number", name="uq_decisions_run_number"),)
