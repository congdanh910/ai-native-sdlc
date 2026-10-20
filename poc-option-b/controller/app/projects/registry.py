"""Register projects and check whether they are ready for the team."""

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import shutil

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.config import DATA_DIR, get_settings
from controller.app.doing import github
from controller.app.doing.github import repo_slug
from controller.app.doing.validation import ValidationGate
from controller.app.events import record_event
from controller.app.models import Project
from controller.app.project_docs import SDS_PATH, SRS_PATH, SDS_STORY_LINE, srs_stories
from controller.app.projects.config import ProjectConfig, effective_policy, parse_project_config
from controller.app.projects.repo import GitError, Mirror, _git, validate_repo_url

KEY = re.compile(r"[a-z][a-z0-9-]{1,39}")
CONFIG_PATH = ".sdlc/project.yaml"
AGENTS_PATH = "AGENTS.md"
CLAUDE_PATH = "CLAUDE.md"
REVIEW_PATH = "REVIEW.md"
CONSTITUTION_PATHS = (".specify/memory/constitution.md", "constitution.md")


@dataclass(frozen=True)
class Item:
    name: str
    status: str  # ok | warn | fail | pending
    detail: str


@dataclass(frozen=True)
class Snapshot:
    """The onboarding files of a project at one commit."""
    commit: str
    config: ProjectConfig
    agents_md: str
    review_md: str | None
    constitution_md: str | None = None


class ProjectError(ValueError):
    pass


def mirror_for(project: Project) -> Mirror:
    return Mirror(Path(project.mirror_path))


def add_project(session: Session, key: str, repo_url: str, actor: str, branch: str | None = None) -> Project:
    if not KEY.fullmatch(key):
        raise ProjectError("key must be 2-40 lowercase letters, digits or '-', starting with a letter")
    if session.scalar(select(Project).where(Project.key == key)):
        raise ProjectError(f"project {key} already exists")
    try:
        url = validate_repo_url(repo_url.strip())
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc
    mirror = Mirror(DATA_DIR / "projects" / key / "mirror.git")
    if mirror.path.exists():
        raise ProjectError(f"a mirror for {key} already exists at {mirror.path}; remove it first")
    try:
        mirror.clone(url)
        branch = branch.strip() if branch and branch.strip() else mirror.default_branch()
        mirror.resolve(branch)
    except (GitError, ValueError) as exc:
        shutil.rmtree(mirror.path, ignore_errors=True)
        raise ProjectError(f"cannot use repository: {exc}") from exc
    project = Project(key=key, repo_url=url, default_branch=branch, mirror_path=str(mirror.path),
                      readiness="not_ready", readiness_report={})
    session.add(project)
    session.flush()
    record_event(session, "project.added", actor=actor, acting_role="platform_admin",
                 project_id=project.id, payload={"repo_url": url, "default_branch": branch})
    check_project(session, project, actor=actor)
    return project


def read_snapshot(mirror: Mirror, commit: str) -> tuple[Snapshot | None, list[Item]]:
    """Load and validate onboarding files at a commit. Returns the snapshot only if usable."""
    items: list[Item] = []
    agents = mirror.read_file(commit, AGENTS_PATH)
    items.append(Item("AGENTS.md", "ok" if agents else "fail",
                      "present" if agents else "missing or empty; it is the knowledge file for every AI role"))
    claude = mirror.read_file(commit, CLAUDE_PATH)
    if claude and "@AGENTS.md" in claude:
        items.append(Item("CLAUDE.md", "ok", "imports AGENTS.md"))
    else:
        items.append(Item("CLAUDE.md", "warn", "should contain `@AGENTS.md` so Claude Code reads the same knowledge"))
    review = mirror.read_file(commit, REVIEW_PATH)
    items.append(Item("REVIEW.md", "ok" if review else "warn",
                      "present" if review else "optional; the Code Reviewer uses platform defaults"))
    constitution_path = next((p for p in CONSTITUTION_PATHS if mirror.read_file(commit, p)), None)
    constitution = mirror.read_file(commit, constitution_path) if constitution_path else None
    items.append(Item("constitution", "ok" if constitution else "warn",
                      f"{constitution_path}; every AI role and the Code Reviewer follow it" if constitution else
                      "optional; add .specify/memory/constitution.md (architecture, API, naming, security rules) "
                      "so every role and the Code Reviewer follow the project's rules"))
    srs = mirror.read_file(commit, SRS_PATH)
    if srs:
        stories = srs_stories(srs)
        items.append(Item(SRS_PATH, "ok" if stories else "warn",
                          f"present; {len(stories)} user stories in section 7" if stories
                          else "present, but section 7 lists no user stories"))
    else:
        items.append(Item(SRS_PATH, "warn", "missing; start an SRS request (New request, Project SRS) "
                                            "before feature runs"))
    sds = mirror.read_file(commit, SDS_PATH)
    if sds:
        items.append(Item(SDS_PATH, "ok", f"present; {len(SDS_STORY_LINE.findall(sds))} user stories in section 5"))
    else:
        items.append(Item(SDS_PATH, "warn", "missing; after the SRS is merged, start an SDS request "
                                            "(New request, Project SDS) before feature runs"))

    config = None
    raw = mirror.read_file(commit, CONFIG_PATH)
    if raw is None:
        items.append(Item(CONFIG_PATH, "fail", "missing; copy examples/task-board/.sdlc/project.yaml and adapt it"))
    else:
        try:
            config = parse_project_config(raw)
            effective_policy(config)
            items.append(Item(CONFIG_PATH, "ok", f"valid; {len(config.validation)} validation commands"))
        except (ValidationError, ValueError) as exc:
            items.append(Item(CONFIG_PATH, "fail", _first_error(exc)))
            config = None
    if config is None or not agents:
        return None, items
    return Snapshot(commit, config, agents, review, constitution), items


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "file"
        return f"{location}: {error['msg']}"
    return str(exc).splitlines()[0]


def doing_checks(project: Project, mirror: Mirror, commit: str | None, snapshot: Snapshot | None) -> list[Item]:
    """What coding needs: validation passing on the default branch in its container, and GitHub access."""
    if get_settings().coding_is_mocked:
        return [Item("validation commands", "pending", "checked when SDLC_DOING_MODE=cli"),
                Item("GitHub access", "pending", "checked when SDLC_DOING_MODE=cli")]
    if snapshot is None:
        return []
    items: list[Item] = []
    policy = effective_policy(snapshot.config)
    if not policy.validation_image and not policy.env_dockerfile:
        items.append(Item("validation image", "fail",
                          f"{snapshot.config.validation_image!r} is not a toolchain this platform provides"))
    else:
        checkout = DATA_DIR / "checks" / project.key / commit[:12]
        if checkout.exists():
            _git("--git-dir", str(mirror.path), "worktree", "remove", "--force", str(checkout))
        _git("--git-dir", str(mirror.path), "worktree", "add", "--quiet", "--detach", str(checkout), commit)
        try:
            gate = ValidationGate(policy, checkout, DATA_DIR / "logs" / "checks" / project.key / commit[:12])
            for report in gate.run([c.name for c in policy.validation], "check"):
                items.append(Item(f"validation: {report.name}", "ok" if report.ok else "fail",
                                  f"passed in {report.seconds}s ({policy.validation_image or policy.env_dockerfile})"
                                  if report.ok else
                                  f"exit {report.exit_code}{' (timed out)' if report.timed_out else ''}; "
                                  f"log {report.log_path}"))
        finally:
            _git("--git-dir", str(mirror.path), "worktree", "remove", "--force", str(checkout))
    slug = repo_slug(project.repo_url)
    if slug is None:
        items.append(Item("GitHub access", "warn", "local repository: pull requests stay simulated in the mirror"))
    else:
        try:
            items.append(Item("GitHub access", "ok", github.check_access(slug)))
        except github.GitHubError as exc:
            items.append(Item("GitHub access", "fail", str(exc)))
    return items


def check_project(session: Session, project: Project, actor: str) -> list[Item]:
    mirror = mirror_for(project)
    try:
        mirror.fetch()
        commit = mirror.resolve(project.default_branch)
    except (GitError, ValueError) as exc:
        items = [Item("repository", "fail", f"cannot read {project.default_branch}: {exc}")]
        commit = None
    else:
        snapshot, items = read_snapshot(mirror, commit)
        items.insert(0, Item("repository", "ok", f"{project.default_branch} at {commit[:12]}"))
        if snapshot and snapshot.config.project != project.key:
            items.append(Item("project key", "fail",
                              f"project.yaml says {snapshot.config.project!r}, registered as {project.key!r}"))
    items += doing_checks(project, mirror, commit, snapshot if commit else None)
    statuses = {item.status for item in items}
    readiness = "not_ready" if "fail" in statuses else "plan_only" if "pending" in statuses else "ready"
    project.readiness = readiness
    project.readiness_report = {"items": [asdict(item) for item in items]}
    project.checked_commit = commit
    record_event(session, "project.checked", actor=actor, acting_role="platform_admin", project_id=project.id,
                 payload={"readiness": readiness, "commit": commit})
    return items
