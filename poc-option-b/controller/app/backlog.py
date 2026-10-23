"""The story backlog of a project: which user story is delivered after which.

The Tech Lead AI proposes the order and the dependencies from the SRS and the SDS; the controller checks them (every
story once, no unknown ids, no cycles) and works out each story's state from its runs; the Tech Lead edits and approves.
A story is ready when every story it depends on is merged (its run COMPLETED) or was done outside the platform.
"""

from datetime import datetime, timezone
import re

from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.events import record_event
from controller.app.models import Backlog, Document, Project, Run
from controller.app.project_docs import SDS_PATH, SRS_PATH, srs_features
from controller.app.projects.registry import mirror_for
from controller.app.schemas import Contract, FeedbackReply, Intent
from controller.app.states import TERMINAL, HumanRole, RunKind, RunStatus

DRAFT, APPROVED = "draft", "approved"


class BacklogItem(Contract):
    story: str = Field(min_length=3)
    depends_on: list[str] = []
    reason: str = ""  # why it comes where it does: what it needs from the stories it depends on
    hold: bool = False  # never opened on its own
    done: str = ""  # delivered outside the platform (the Foundation, by hand): why


class ProposedItem(Contract):
    story: str = Field(min_length=3)
    depends_on: list[str] = []
    reason: str = ""


class BacklogProposal(Contract):
    """The Tech Lead AI's answer: every story in the order to deliver it."""
    items: list[ProposedItem] = Field(min_length=1)
    notes: str = ""
    feedback_replies: list[FeedbackReply] = []


class BacklogError(ValueError):
    pass


def _section(text: str, number: str) -> str:
    """One `## <number>.` section of a rendered SRS or SDS."""
    match = re.search(rf"^##\s+{re.escape(number)}\.\s.*?$(.*?)(?=^##\s+\d+\.\s|\Z)", text or "", re.MULTILINE | re.DOTALL)
    return match.group(0).strip() if match else ""


def ai_context(srs: str, sds: str, current: list[dict], feedback: str) -> dict:
    """What the Tech Lead AI reads: the stories, the domain and flows of the SRS, the domain model and APIs of the SDS."""
    stories = [{"id": s.id, "feature": f.name, "title": s.title, "actors": s.actors, "statement": s.statement.replace("**", "")}
               for f in srs_features(srs) for s in f.stories]
    apis = [line for line in (sds or "").splitlines() if re.match(r"^\|\s*`?API-[A-Z]+-\d+`?\s*\|", line)]
    return {"stories": stories, "srs_domain": _section(srs, "2"), "srs_flows": _section(srs, "6"),
            "sds_domain": _section(sds, "2"), "sds_apis": "\n".join(apis),
            "current": current, "feedback": feedback}


def repair(data: dict) -> dict:
    """Ids as plain ids: models wrap them in backticks or add the title after them."""
    def plain(value) -> str:
        found = re.search(r"[A-Z]{2,4}-US-\d+", str(value))
        return found.group(0) if found else str(value).strip().strip("`")
    for item in data.get("items") or []:
        if isinstance(item, dict):
            item["story"] = plain(item.get("story", ""))
            item["depends_on"] = [plain(d) for d in item.get("depends_on") or []]
    return data


def find_cycle(items: list[dict]) -> list[str]:
    """A dependency cycle as story ids (the first repeated at the end), or [] when there is none."""
    deps = {i["story"]: [d for d in i.get("depends_on") or []] for i in items}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(story: str) -> list[str]:
        state[story] = 1
        stack.append(story)
        for dep in deps.get(story, []):
            if dep not in deps:
                continue
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[story] = 2
        return []
    for story in deps:
        if story not in state:
            found = visit(story)
            if found:
                return found
    return []


def check(items: list[dict], stories: list[str]) -> list[str]:
    """What stops the backlog from being approved."""
    problems: list[str] = []
    seen: set[str] = set()
    for item in items:
        story = item["story"]
        if story in seen:
            problems.append(f"{story} is in the backlog twice")
        seen.add(story)
        if story not in stories:
            problems.append(f"{story} is not a user story of the SRS")
        for dep in item.get("depends_on") or []:
            if dep == story:
                problems.append(f"{story} depends on itself")
            elif dep not in stories:
                problems.append(f"{story} depends on {dep}, which is not a user story of the SRS")
    missing = [s for s in stories if s not in seen]
    if missing:
        problems.append(f"not in the backlog: {', '.join(missing)}")
    cycle = find_cycle(items)
    if cycle:
        problems.append(f"dependency cycle: {' → '.join(cycle)}")
    return problems


def waves(items: list[dict]) -> dict[str, int]:
    """Wave 1 depends on nothing; a story's wave is one more than the latest wave it depends on. Stories in a cycle
    get no wave."""
    deps = {i["story"]: [d for d in i.get("depends_on") or []] for i in items}
    result: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for story, needs in deps.items():
            if story in result:
                continue
            known = [d for d in needs if d in deps]
            if all(d in result for d in known):
                result[story] = 1 + max((result[d] for d in known), default=0)
                changed = True
    return result


def _srs(project: Project) -> tuple[str, str]:
    if not project.checked_commit:
        raise BacklogError(f"{project.key} has not been checked; run the project check first")
    mirror = mirror_for(project)
    srs = mirror.read_file(project.checked_commit, SRS_PATH) or ""
    if not srs_features(srs):
        raise BacklogError(f"{project.key} has no SRS.md with user stories on {project.default_branch}")
    return srs, mirror.read_file(project.checked_commit, SDS_PATH) or ""


def _story_ids(srs: str) -> list[str]:
    return [s.id for f in srs_features(srs) for s in f.stories]


def story_runs(session: Session, project: Project) -> dict[str, list[Run]]:
    """The feature runs of each story, oldest first."""
    runs: dict[str, list[Run]] = {}
    rows = session.execute(select(Run, Document.data).join(Document, Document.run_id == Run.id)
                           .where(Run.project_id == project.id, Run.kind == RunKind.FEATURE, Run.experiment.is_(False),
                                  Document.kind == "intent", Document.version == 1).order_by(Run.created_at)).all()
    for run, data in rows:
        story = (data or {}).get("story")
        if story:
            runs.setdefault(story, []).append(run)
    return runs


def foundation_blocker(session: Session, project: Project) -> str:
    """Stories wait for the project's Foundation when it has one that is not merged yet."""
    statuses = session.scalars(select(Run.status).where(Run.project_id == project.id, Run.kind == RunKind.FOUNDATION,
                                                        Run.experiment.is_(False))).all()
    if statuses and RunStatus.COMPLETED not in statuses:
        return "the Foundation is not merged"
    return ""


def states(items: list[dict], runs: dict[str, list[Run]], foundation: str) -> dict[str, dict]:
    """delivered / running / held / blocked / ready for every story of the backlog, with what decides it."""
    delivered = {i["story"] for i in items if i.get("done")}
    delivered |= {story for story, rs in runs.items() if any(r.status == RunStatus.COMPLETED for r in rs)}
    result: dict[str, dict] = {}
    for item in items:
        story, last = item["story"], (runs.get(item["story"]) or [None])[-1]
        run = {"id": str(last.id), "status": last.status} if last else None
        if story in delivered:
            state = {"state": "delivered", "detail": item.get("done") or "merged"}
        elif last is not None and last.status not in TERMINAL:
            state = {"state": "running", "detail": last.status}
        elif item.get("hold"):
            state = {"state": "held", "detail": "on hold"}
        else:
            waiting = [d for d in item.get("depends_on") or [] if d not in delivered]
            if foundation:
                state = {"state": "blocked", "detail": foundation, "waiting_for": waiting}
            elif waiting:
                state = {"state": "blocked", "detail": f"waits for {', '.join(waiting)}", "waiting_for": waiting}
            else:
                state = {"state": "ready", "detail": "every story it depends on is merged" if item.get("depends_on")
                         else "depends on no other story"}
        result[story] = {**state, "run": run}
    return result


def get_backlog(session: Session, project: Project, lock: bool = False) -> Backlog | None:
    query = select(Backlog).where(Backlog.project_id == project.id)
    return session.scalar(query.with_for_update() if lock else query)


def view(session: Session, project: Project) -> dict:
    srs, _ = _srs(project)
    stories = {s.id: {"title": s.title, "actors": s.actors, "feature": f.name, "statement": s.statement.replace("**", "")}
               for f in srs_features(srs) for s in f.stories}
    backlog = get_backlog(session, project)
    items = backlog.items if backlog else []
    found = states(items, story_runs(session, project), foundation_blocker(session, project))
    wave = waves(items)
    return {
        "project": project.key, "commit": project.checked_commit,
        "status": backlog.status if backlog else None, "version": backlog.version if backlog else 0,
        "notes": backlog.notes if backlog else "", "approved_by": backlog.approved_by if backlog else None,
        "approved_at": backlog.approved_at.isoformat() if backlog and backlog.approved_at else None,
        "srs_commit": backlog.srs_commit if backlog else None,
        "items": [{**item, **stories.get(item["story"], {"title": "", "actors": "", "feature": "", "statement": ""}),
                   "in_srs": item["story"] in stories, "wave": wave.get(item["story"]), **found[item["story"]]}
                  for item in items],
        "not_in_backlog": [{"story": sid, **meta} for sid, meta in stories.items()
                           if sid not in {i["story"] for i in items}],
        "problems": check(items, list(stories)) if backlog else [],
        "delivery": _delivery_view(session, project),
    }


def _delivery_view(session: Session, project: Project) -> dict:
    settings = delivery(project)
    return {"auto_start": bool(settings and settings.auto_start), "max_open_runs": settings.max_open_runs if settings else None,
            "open_runs": len(open_runs(session, project)), "config_valid": settings is not None}


def _store(session: Session, project: Project, items: list[dict], notes: str, actor: str, srs_commit: str | None,
           version: int | None) -> Backlog:
    backlog = get_backlog(session, project, lock=True)
    if backlog is not None and version is not None and version != backlog.version:
        raise BacklogError(f"the backlog changed (now version {backlog.version}); reload it and edit again")
    if backlog is None:
        backlog = Backlog(project_id=project.id, items=items, notes=notes, status=DRAFT, version=1,
                          srs_commit=srs_commit, proposed_by=actor)
        session.add(backlog)
    else:
        backlog.items, backlog.notes, backlog.status = items, notes, DRAFT
        backlog.version += 1
        backlog.srs_commit, backlog.proposed_by = srs_commit, actor
        backlog.approved_by = backlog.approved_at = None
    session.flush()
    return backlog


def proposal_context(session: Session, project: Project, feedback: str = "") -> dict:
    """What the Tech Lead AI is asked with; the model is called outside the transaction."""
    srs, sds = _srs(project)
    backlog = get_backlog(session, project)
    return ai_context(srs, sds, backlog.items if backlog else [], feedback)


def store_proposal(session: Session, project: Project, answer: BacklogProposal, *, actor: str,
                   feedback: str = "") -> Backlog:
    """Every story in the AI's order; what it leaves out goes at the end, and the human's hold and done marks stay."""
    srs, _ = _srs(project)
    backlog = get_backlog(session, project)
    kept = {i["story"]: i for i in (backlog.items if backlog else [])}
    ids = _story_ids(srs)
    items, seen = [], set()
    for proposed in answer.items:
        if proposed.story in seen or proposed.story not in ids:
            continue  # a story twice or one the SRS does not have: the model's slip, not the Tech Lead's problem
        seen.add(proposed.story)
        previous = kept.get(proposed.story, {})
        items.append(BacklogItem(story=proposed.story, depends_on=list(dict.fromkeys(proposed.depends_on)),
                                 reason=proposed.reason, hold=previous.get("hold", False),
                                 done=previous.get("done", "")).model_dump())
    items += [BacklogItem(story=s, reason="not placed by the AI").model_dump() for s in ids if s not in seen]
    notes = answer.notes + "".join(f"\n- {r.feedback}: {r.change}" for r in answer.feedback_replies)
    stored = _store(session, project, items, notes.strip(), actor, project.checked_commit, None)
    record_event(session, "backlog.proposed", actor=actor, acting_role=HumanRole.TECH_LEAD, project_id=project.id,
                 payload={"version": stored.version, "feedback": feedback, "stories": len(items)})
    return stored


def save(session: Session, project: Project, items: list[dict], notes: str, *, actor: str, version: int) -> Backlog:
    """The Tech Lead's edits: the order, dependencies, hold and done. The backlog is a draft again."""
    _srs(project)
    try:
        parsed = [BacklogItem.model_validate(i).model_dump() for i in items]
    except ValidationError as exc:
        raise BacklogError(f"an item of the backlog is not valid: {exc.errors()[0]['msg']}") from exc
    stored = _store(session, project, parsed, notes, actor, project.checked_commit, version)
    record_event(session, "backlog.saved", actor=actor, acting_role=HumanRole.TECH_LEAD, project_id=project.id,
                 payload={"version": stored.version})
    return stored


def approve(session: Session, project: Project, *, actor: str, version: int, reason: str = "") -> Backlog:
    srs, _ = _srs(project)
    backlog = get_backlog(session, project, lock=True)
    if backlog is None:
        raise BacklogError(f"{project.key} has no backlog yet; propose one first")
    if version != backlog.version:
        raise BacklogError(f"the backlog changed (now version {backlog.version}); reload it before approving")
    problems = check(backlog.items, _story_ids(srs))
    if problems:
        raise BacklogError("fix the backlog first: " + "; ".join(problems))
    backlog.status, backlog.approved_by, backlog.approved_at = APPROVED, actor, datetime.now(timezone.utc)
    record_event(session, "backlog.approved", actor=actor, acting_role=HumanRole.TECH_LEAD, project_id=project.id,
                 payload={"version": backlog.version, "reason": reason})
    return backlog


def story_intent(srs: str, sds: str, story: str) -> Intent:
    """A feature request for one story: its title, its statement as the problem, the outcomes the SRS and SDS give."""
    from controller.app.outcomes import story_context, suggest
    found = next((s for f in srs_features(srs) for s in f.stories if s.id == story), None)
    if found is None:
        raise BacklogError(f"{story} is not a user story of the SRS")
    statement = found.statement.replace("**", "").replace("`", "") or found.title
    lines = suggest(story_context(srs, sds, story))
    outcome = "\n".join(f"- {line['text']}" for line in lines) or f"- {statement}"
    return Intent(title=found.title, problem=statement, outcome=outcome, users_and_systems=found.actors, story=story)


def start(session: Session, project: Project, story: str, *, actor: str):
    """Open the run of a ready story; it stops at every gate like a run made by hand."""
    from controller.app import services
    srs, sds = _srs(project)
    backlog = get_backlog(session, project)
    if backlog is None:
        raise BacklogError(f"{project.key} has no backlog yet; propose one first")
    runs = story_runs(session, project)
    state = states(backlog.items, runs, foundation_blocker(session, project)).get(story)
    if state is None:
        raise BacklogError(f"{story} is not in the backlog")
    if state["state"] != "ready":
        raise BacklogError(f"{story} is {state['state']}: {state['detail']}")
    key = f"backlog:{project.key}:{story}:{len(runs.get(story, [])) + 1}"
    run = services.create_run(session, project, story_intent(srs, sds, story), actor=actor, idempotency_key=key)
    record_event(session, "backlog.started", actor=actor, project_id=project.id, run_id=run.id,
                 payload={"story": story})
    return run


# ---------------------------------------------------------------- B2: opening stories on their own, waiting for a run

AUTO_ACTOR = "delivery_coordinator"
WAIT_FOR = "wait_for"  # artifact: the runs a run waits for before it codes (the Tech Lead's choice at the plan gate)
# files the platform writes for every story; B3 rebuilds a story's sections on main, so they are no overlap
PLATFORM_PATHS = ("specs/", ".sdlc/", "SRS.md", "SDS.md")


def delivery(project: Project):
    """The project's delivery settings, or None when its project.yaml does not parse."""
    from controller.app.foundation import CONFIG_PATH
    from controller.app.projects.config import Delivery, parse_project_config
    raw = mirror_for(project).read_file(project.checked_commit, CONFIG_PATH) if project.checked_commit else None
    try:
        return parse_project_config(raw).delivery if raw else Delivery()
    except ValueError:
        return None


def open_runs(session: Session, project: Project) -> list[Run]:
    """The project's feature runs that are not finished (experiments aside)."""
    return list(session.scalars(select(Run).where(Run.project_id == project.id, Run.kind == RunKind.FEATURE,
                                                  Run.experiment.is_(False),
                                                  Run.status.not_in([s.value for s in TERMINAL]))))


def _note_failure(session: Session, project: Project, story: str, message: str) -> None:
    """Once per message: the worker tries again at every poll."""
    from controller.app.models import Event
    last = session.scalar(select(Event.payload).where(Event.project_id == project.id,
                                                      Event.event_type == "backlog.start_failed")
                          .order_by(Event.id.desc()).limit(1))
    if last != {"story": story, "error": message}:
        record_event(session, "backlog.start_failed", actor=AUTO_ACTOR, project_id=project.id,
                     payload={"story": story, "error": message})


def auto_start(session: Session) -> list[str]:
    """Open the next ready stories of every approved backlog while the project has room (delivery.max_open_runs).
    A story opens on its own once: after a run that did not merge, a person starts it again."""
    from controller.app import services
    started = []
    for backlog in session.scalars(select(Backlog).where(Backlog.status == APPROVED)).all():
        project = session.get(Project, backlog.project_id)
        settings = delivery(project)
        if settings is None or not settings.auto_start or project.readiness == "not_ready":
            continue
        room = settings.max_open_runs - len(open_runs(session, project))
        if room <= 0:
            continue
        runs = story_runs(session, project)
        found = states(backlog.items, runs, foundation_blocker(session, project))
        for item in backlog.items:
            if room <= 0:
                break
            story = item["story"]
            if found[story]["state"] != "ready" or runs.get(story):
                continue
            try:
                with session.begin_nested():
                    run = start(session, project, story, actor=AUTO_ACTOR)
            except (BacklogError, services.DecisionError, ValueError) as exc:
                _note_failure(session, project, story, str(exc)[:500])
                continue
            room -= 1
            started.append(f"{project.key}: opened {story} as run {str(run.id)[:8]}")
    return started


def _files(session: Session, run: Run) -> set[str]:
    """What a run changes or plans to change: its latest plan's files and what its branch changed since its base."""
    from controller.app.documents import latest_plan
    from controller.app.doing import changes
    files: set[str] = set()
    plan = latest_plan(session, run.id)
    for task in (plan.payload.get("tasks") or []) if plan else []:
        files.update(task.get("modify", []) + task.get("create", []))
    if run.worktree_path and run.base_commit:
        from pathlib import Path
        from controller.app.coordinator import diff_base
        if Path(run.worktree_path).is_dir():
            try:
                files.update(changes.changed_since(Path(run.worktree_path), diff_base(session, run)))
            except Exception:  # a worktree being rebuilt: the plan's files still count
                pass
    return {f for f in files if not f.startswith(PLATFORM_PATHS)}


def overlaps(session: Session, run: Run) -> list[dict]:
    """Open runs of the project that change files this run's plan changes (the platform's own files aside)."""
    from controller.app.documents import latest_plan
    plan = latest_plan(session, run.id)
    mine = {f for task in (plan.payload.get("tasks") or []) if plan
            for f in task.get("modify", []) + task.get("create", []) if not f.startswith(PLATFORM_PATHS)}
    if not mine:
        return []
    others = session.scalars(select(Run).where(Run.project_id == run.project_id, Run.id != run.id,
                                               Run.kind.in_([RunKind.FEATURE, RunKind.FOUNDATION]),
                                               Run.experiment.is_(False),
                                               Run.status.not_in([s.value for s in TERMINAL]))
                             .order_by(Run.created_at)).all()
    found = []
    for other in others:
        shared = sorted(mine & _files(session, other))
        if shared:
            found.append({"run": str(other.id), "title": other.title, "status": other.status, "files": shared})
    return found


def waiting_on(session: Session, run: Run) -> list[Run]:
    """The runs this run still waits for: chosen at the plan gate, not finished yet."""
    import json
    import uuid
    from controller.app.coordinator import artifact
    chosen = json.loads(artifact(session, run.id, WAIT_FOR) or "[]")
    runs = [session.get(Run, uuid.UUID(r)) for r in chosen]
    return [r for r in runs if r is not None and r.status not in TERMINAL]


def wait_reason(runs: list[Run]) -> str:
    return "waits for " + ", ".join(f"run {str(r.id)[:8]} ({r.title})" for r in runs) + \
        " to finish before coding: they change the same files"


def _busy(session: Session, run: Run) -> bool:
    from controller.app.models import Execution
    return session.scalar(select(Execution.id).where(Execution.run_id == run.id,
                                                     Execution.status.in_(("queued", "running"))).limit(1)) is not None


def sync_with_main(session: Session) -> list[str]:
    """Queue a merge of the default branch into every open run's branch that waits at a gate and lacks main's latest
    commit (delivery.update_with_main). The mirror is fetched first, so merges done outside the platform count."""
    from pathlib import Path
    from controller.app import coordinator, mainline
    queued = []
    states_ = [s.value for s in coordinator.SYNC_BEFORE_CODING + coordinator.SYNC_AFTER_CODING]
    runs = session.scalars(select(Run).where(Run.kind.in_([RunKind.FEATURE, RunKind.FOUNDATION]),
                                             Run.experiment.is_(False), Run.status.in_(states_),
                                             Run.worktree_path.is_not(None))).all()
    mains: dict = {}
    for run in runs:
        project = session.get(Project, run.project_id)
        if project.id not in mains:
            settings = delivery(project)
            mains[project.id] = None
            if settings is not None and settings.update_with_main:
                mirror = mirror_for(project)
                try:
                    mirror.fetch()
                    mains[project.id] = mirror.resolve(project.default_branch)
                except Exception as exc:  # offline: the next poll tries again
                    _note_failure(session, project, "main", f"fetching {project.default_branch} failed: {exc}"[:500])
        main = mains[project.id]
        if not main or not Path(run.worktree_path).is_dir() or _busy(session, run):
            continue
        if mainline.is_up_to_date(Path(run.worktree_path), main):
            continue
        coordinator.queue(session, run, "update_branch", key=f"main:{main}:{run.status}")
        queued.append(f"run {str(run.id)[:8]}: merging {main[:12]} from {project.default_branch}")
    return queued


def release_waiting(session: Session) -> list[str]:
    """Runs held at READY for other runs start coding once those runs are finished, and once main (with what they
    merged) is merged into their branch."""
    from controller.app import services
    released = []
    for run in session.scalars(select(Run).where(Run.status == RunStatus.READY).with_for_update(skip_locked=True)).all():
        if _busy(session, run):
            continue  # its update with main runs first
        if run.status_reason and run.status_reason.startswith("waits for ") and not waiting_on(session, run):
            services.start_coding(session, run, actor=AUTO_ACTOR, acting_role=None)
            released.append(f"run {str(run.id)[:8]} starts coding: the runs it waited for are finished")
    return released


def tick(session: Session) -> list[str]:
    """The worker's poll: bring open branches up with main, release the runs that waited, open the next stories."""
    return sync_with_main(session) + release_waiting(session) + auto_start(session)


def _story_section(text: str, story: str) -> str:
    """The `#### <n> <story>: ...` section of a rendered SRS (section 7) or SDS (section 5), anchors removed."""
    match = re.search(rf"^####\s+[\d.]+\s+{re.escape(story)}:.*?(?=^#{{2,4}}\s|\Z)", text or "", re.MULTILINE | re.DOTALL)
    return re.sub(r"\s*<a id=\"[^\"]*\"></a>", "", match.group(0)).strip() if match else ""


def story_detail(session: Session, project: Project, story: str) -> dict:
    """One story as the backlog shows it opened: what the SRS and SDS say, its place in the backlog, its runs, and
    the accepted spec of a delivered story (specs/<feature>/spec.md on the default branch)."""
    from controller.app.feature_docs import US_HEADING
    from controller.app.outcomes import story_context
    srs, sds = _srs(project)
    if story not in _story_ids(srs):
        raise BacklogError(f"{story} is not a user story of the SRS")
    mirror = mirror_for(project)
    spec_path, spec = "", ""
    for path in mirror.list_files(project.checked_commit):
        if path.startswith("specs/") and path.endswith("/spec.md"):
            text = mirror.read_file(project.checked_commit, path) or ""
            section = next((m for m in US_HEADING.finditer(text) if m[1] == story), None)
            if section:
                following = [m.start() for m in US_HEADING.finditer(text) if m.start() > section.start()]
                spec_path = path
                spec = re.sub(r"\n+---\s*$", "", text[section.start(): following[0] if following else len(text)].rstrip())
                break
    backlog = get_backlog(session, project)
    item = next((i for i in (backlog.items if backlog else []) if i["story"] == story), None)
    needed_by = [i["story"] for i in (backlog.items if backlog else []) if story in (i.get("depends_on") or [])]
    context = story_context(srs, sds, story)
    return {"story": story, "srs": _story_section(srs, story), "sds": _story_section(sds, story),
            "requirements": context["requirements"], "apis": context["apis"], "errors": context["errors"],
            "spec_path": spec_path, "spec": spec, "item": item, "needed_by": needed_by,
            "runs": [{"id": str(r.id), "status": r.status, "title": r.title, "created_at": r.created_at.isoformat()}
                     for r in story_runs(session, project).get(story, [])]}
