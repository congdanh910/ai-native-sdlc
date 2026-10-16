"""Discovery decisions of the operator: start a discovery, add and remove sources."""

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.config import DATA_DIR, get_settings
from controller.app.documents import latest_document
from controller.app.discovery.sources import SourceError, prepare
from controller.app.events import record_event
from controller.app.state_machine import transition_run
from controller.app.models import Artifact, Project, Run, Source
from controller.app.projects.config import Discovery, parse_project_config
from controller.app.states import RunKind, RunStatus

OPEN_FOR_SOURCES = (RunStatus.INGESTING, RunStatus.CLARIFYING)


class DiscoveryRequest(BaseModel):
    """What starts a discovery: a name and, optionally, what the project is about in a few lines."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=200)
    notes: str = ""


def render_request(request: DiscoveryRequest) -> str:
    return (f"# Discovery: {request.title}\n\n## Notes\n\n{request.notes.strip() or '_None._'}\n\n"
            "Sources are listed in the run; the Business Analyst extracts the requirement inventory from them.\n")


def discovery_config(session: Session, run: Run) -> Discovery:
    """The discovery section of the project's .sdlc/project.yaml as it was at the run's preflight."""
    raw = session.scalar(select(Artifact.content).where(Artifact.run_id == run.id, Artifact.kind == "project_config")
                         .order_by(Artifact.created_at.desc()).limit(1))
    return parse_project_config(raw).discovery if raw else Discovery()


def source_dir(project: Project, run: Run):
    return DATA_DIR / "projects" / project.key / "sources" / str(run.id)[:8]


def _require_open(run: Run) -> None:
    if run.kind != RunKind.DISCOVERY:
        raise ValueError("sources belong to a discovery run")
    if run.status not in OPEN_FOR_SOURCES:
        raise ValueError(f"sources can be changed while the discovery is {' or '.join(OPEN_FOR_SOURCES)}; "
                         f"it is {run.status}")


PURPOSES = ("requirements", "minutes")


def add_source(session: Session, run: Run, filename: str, data: bytes, *, actor: str,
               purpose: str = "requirements") -> Source:
    """A raw requirement source, or minutes of a meeting (an email, a chat) whose decisions the Business Analyst
    reads at the next extraction."""
    _require_open(run)
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {', '.join(PURPOSES)}")
    config = discovery_config(session, run)
    accepted = tuple(t.strip().lower() for t in get_settings().discovery_accepted_types.split(",") if t.strip())
    try:
        prepared = prepare(filename, data, accepted=accepted, max_bytes=config.limits.max_source_mb * 1_000_000,
                           max_pages=config.limits.max_pages_per_source)
    except SourceError as exc:
        raise ValueError(str(exc)) from exc
    existing = session.scalar(select(Source).where(Source.run_id == run.id, Source.sha256 == prepared.sha256))
    if existing and not existing.removed:
        raise ValueError(f"{prepared.filename} has the same content as {existing.filename}, already added")
    folder = source_dir(session.get(Project, run.project_id), run)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{prepared.sha256[:12]}-{prepared.filename}"
    path.write_bytes(data)
    text_path = None
    if prepared.text is not None and prepared.kind != "text":
        text_path = path.with_name(path.name + ".md")
        text_path.write_text(prepared.text)
    if existing:  # the same file was removed earlier and is added again
        existing.removed, existing.filename, existing.uploaded_by = False, prepared.filename, actor
        existing.purpose = purpose
        source = existing
    else:
        source = Source(run_id=run.id, filename=prepared.filename, kind=prepared.kind, sha256=prepared.sha256,
                        size=prepared.size, pages=prepared.pages, path=str(path),
                        text_path=str(text_path) if text_path else None, uploaded_by=actor, purpose=purpose)
        session.add(source)
    session.flush()
    record_event(session, "source.added", actor=actor, acting_role="product_owner", run_id=run.id,
                 payload={"source": str(source.id), "filename": source.filename, "kind": source.kind,
                          "sha256": source.sha256, "pages": source.pages, "size": source.size, "purpose": purpose})
    return source


def add_text_source(session: Session, run: Run, title: str, text: str, *, actor: str,
                    purpose: str = "requirements") -> Source:
    """Pasted text (an email, chat or minutes) becomes a Markdown source."""
    if not text.strip():
        raise ValueError("paste some text first")
    name = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip().replace(" ", "-")[:80] or "pasted-text"
    return add_source(session, run, f"{name}.md", f"# {title.strip() or name}\n\n{text.strip()}\n".encode(), actor=actor,
                      purpose=purpose)


def remove_source(session: Session, run: Run, source_id: uuid.UUID, *, actor: str, reason: str) -> None:
    """A removed source stays on disk and in the database for the audit trail; extraction skips it."""
    _require_open(run)
    source = session.get(Source, source_id)
    if source is None or source.run_id != run.id or source.removed:
        raise LookupError("source not found")
    if not reason.strip():
        raise ValueError("say why the source is removed")
    source.removed = True
    record_event(session, "source.removed", actor=actor, acting_role="product_owner", run_id=run.id,
                 payload={"source": str(source.id), "filename": source.filename, "sha256": source.sha256,
                          "reason": reason})


def sources(session: Session, run_id: uuid.UUID, *, include_removed: bool = False) -> list[Source]:
    query = select(Source).where(Source.run_id == run_id).order_by(Source.created_at)
    if not include_removed:
        query = query.where(Source.removed.is_(False))
    return list(session.scalars(query))


def start_extraction(session: Session, run: Run, *, actor: str) -> None:
    """The Business Analyst reads what is new (sources, minutes) and applies the accepted decisions, writing the next
    version of the requirement inventory. The project's discovery.limits.max_rounds caps the rounds."""
    from controller.app import coordinator
    from controller.app.discovery import questions as q
    _require_open(run)
    current = sources(session, run.id)
    if not any(s.purpose == "requirements" for s in current):
        raise ValueError("add at least one requirement source first")
    latest = latest_document(session, run.id, "inventory")
    if latest:
        data = latest.data or {}
        ids = {str(s.id) for s in current}
        new_sources = [s for s in current if s.purpose == "requirements" and str(s.id) not in data.get("chunks", {})]
        new_minutes = [s for s in current if s.purpose == "minutes" and str(s.id) not in data.get("minutes_read", [])]
        removed = [sid for sid in data.get("chunks", {}) if sid not in ids]
        accepted = {q.decision_id(d) for d in q.decisions(session, run.id) if d.status == "accepted"}
        new_decisions = accepted - set(data.get("decisions_applied", []))
        written_before_rounds = "chunks" not in data  # its questions are not asked yet
        if not (new_sources or new_minutes or removed or new_decisions or written_before_rounds):
            raise ValueError("nothing new since inventory v%d: add a source or minutes, or answer a question first"
                             % latest.version)
    rounds = latest.version if latest else 0
    limit = discovery_config(session, run).limits.max_rounds
    if rounds >= limit:
        raise ValueError(f"the discovery already had {rounds} extraction round(s), the project's limit "
                         "(discovery.limits.max_rounds); raise it in .sdlc/project.yaml to continue")
    transition_run(session, run, RunStatus.EXTRACTING, actor=actor, acting_role="product_owner",
                   reason=f"extraction round {rounds + 1} of at most {limit}")
    coordinator.queue(session, run, "extract")


def refresh_documents(session: Session, run: Run) -> None:
    from controller.app import coordinator
    names = {str(s.id): s.filename for s in sources(session, run.id, include_removed=True)}
    coordinator.write_discovery_documents(session, run, discovery_config(session, run), names)


def _baseline_inputs(session: Session, run: Run):
    from controller.app.discovery import questions as q
    from controller.app.discovery.inventory import Inventory
    latest = latest_document(session, run.id, "inventory")
    if latest is None:
        raise ValueError("extract the requirement inventory first")
    data = latest.data or {}
    inventory = Inventory.model_validate({"items": data.get("items", []), "gaps": data.get("gaps", []),
                                          "coverage": data.get("coverage", {})})
    run_questions, run_decisions = q.as_dicts(session, run.id)
    return latest, inventory, run_questions, run_decisions


def prepare_baseline(session: Session, run: Run, scope: str, *, actor: str) -> None:
    """The Product Owner states the scope of the phase; the baseline is drafted and waits at its gate."""
    from controller.app.discovery import baseline
    if run.kind != RunKind.DISCOVERY or run.status != RunStatus.CLARIFYING:
        raise ValueError(f"a baseline is prepared while the discovery is CLARIFYING; it is {run.status}")
    if not scope.strip():
        raise ValueError("state the scope of this phase (what the baseline covers)")
    latest, inventory, run_questions, run_decisions = _baseline_inputs(session, run)
    config = discovery_config(session, run)
    files = [{"filename": s.filename, "sha256": s.sha256, "pages": s.pages, "purpose": s.purpose}
             for s in sources(session, run.id)]
    data = baseline.snapshot(run.title.removeprefix("Discovery: "), scope, inventory, latest.version, run_questions,
                             run_decisions, files)
    content = baseline.render(data, {r.id: r.name for r in config.roles})
    from controller.app.documents import add_document
    add_document(session, run.id, "baseline", content, author_kind="human", author=actor, data=data)
    transition_run(session, run, RunStatus.WAITING_BASELINE_APPROVAL, actor=actor, acting_role="product_owner",
                   reason=f"baseline on inventory v{latest.version} waits for the {config.approvers.get('baseline', 'po')} sign-off")


def approve_baseline(session: Session, run: Run, document_id: uuid.UUID, checked: list[str], *, actor: str) -> None:
    """Commit requirements/ to the run's branch and open the draft PR; the controller checks must pass."""
    import json
    from controller.app import coordinator
    from controller.app.discovery import baseline, questions as q
    from controller.app.documents import latest_document as latest_doc
    from controller.app.models import Approval
    if run.status != RunStatus.WAITING_BASELINE_APPROVAL:
        raise ValueError(f"the discovery is {run.status}, not waiting for the baseline")
    document = latest_doc(session, run.id, "baseline")
    if document is None or document.id != document_id:
        raise ValueError("the baseline changed; reload the page")
    evidence = coordinator.require_checklist(session, run, checked)
    if not run.commit_artifacts:
        raise ValueError("the project does not commit artifacts (artifacts.commit_to_repo: false); "
                         "a baseline needs its requirements/ folder in the repository")
    config = discovery_config(session, run)
    current = sources(session, run.id)
    names = {str(s.id): s.filename for s in current}
    files: dict[str, str | bytes] = {
        f"{baseline.BASELINE_DIR}/baseline.md": document.content,
        baseline.BASELINE_JSON: json.dumps(document.data, ensure_ascii=False, indent=1) + "\n",
        f"{baseline.BASELINE_DIR}/inventory.md": latest_doc(session, run.id, "inventory").content,
        f"{baseline.BASELINE_DIR}/questions.md": q.render_questions(q.questions(session, run.id), config, run.title.removeprefix("Discovery: ")),
        f"{baseline.BASELINE_DIR}/decisions.md": q.render_decisions(q.decisions(session, run.id), q.questions(session, run.id),
                                                                    config, names, run.title.removeprefix("Discovery: ")),
        f"{baseline.BASELINE_DIR}/sources.md": baseline.sources_md(current, config.commit_sources),
        **baseline.source_files(current, config.commit_sources),
    }
    project = session.get(Project, run.project_id)
    document.commit_sha = coordinator.worktree_for(project, run).commit_files(
        files, f"sdlc: requirement baseline on inventory v{document.data['inventory_version']}")
    session.add(Approval(run_id=run.id, stage="baseline", decision="approve", actor=actor,
                         acting_role="product_owner", document_id=document.id, checklist=evidence))
    record_event(session, "baseline.approved", actor=actor, acting_role="product_owner", run_id=run.id,
                 payload={"document": str(document.id), "commit": document.commit_sha, "files": sorted(files)})
    transition_run(session, run, RunStatus.PR_OPEN, actor=actor, acting_role="product_owner",
                   reason=f"baseline committed as {document.commit_sha[:12]}; the draft PR opens next")
    coordinator.queue(session, run, "release")


def reject_baseline(session: Session, run: Run, *, actor: str, reason: str) -> None:
    if run.status != RunStatus.WAITING_BASELINE_APPROVAL:
        raise ValueError(f"the discovery is {run.status}, not waiting for the baseline")
    if not reason.strip():
        raise ValueError("say what the baseline still needs")
    from controller.app.models import Approval
    session.add(Approval(run_id=run.id, stage="baseline", decision="reject", actor=actor, acting_role="product_owner",
                         reason=reason, checklist={}))
    record_event(session, "baseline.reject", actor=actor, acting_role="product_owner", run_id=run.id,
                 payload={"reason": reason})
    transition_run(session, run, RunStatus.CLARIFYING, actor=actor, acting_role="product_owner", reason=reason)
