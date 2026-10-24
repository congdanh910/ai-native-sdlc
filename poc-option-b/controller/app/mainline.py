"""Keeping a run's branch up with the default branch (B3): main is merged into the branch, never rebased, so the
commits that evidence, previews, red checks and task_base name stay valid and nothing is force-pushed.

Conflicts come in three kinds:
1. the files the platform writes for every story (specs/<feature>/*.md and sdlc.yaml, the Spec and Plan links in
   SRS.md and SDS.md): main's version is kept and this story's section is put back into it, without a model;
2. migrations: two branches that each add a revision on the same head leave main with two heads; git sees no
   conflict, the migration command fails;
3. code: the Developer resolves it in a task of its own, then validation, review and acceptance run again.
"""

from pathlib import Path
import re
import subprocess

import yaml

from controller.app.feature_docs import PHASE_HEADING, US_HEADING, phase_number, upsert_section
from controller.app.projects.repo import COMMITTER

SECTION_FILES = ("spec.md", "plan.md", "test_cases.md", "checklists/requirements.md")
LINK = re.compile(r"^> (Spec|Plan): \[")
MARKER = re.compile(r"^(<{7}|>{7}) |^={7}$", re.MULTILINE)


class MergeError(RuntimeError):
    pass


def _git(worktree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *COMMITTER, *args], cwd=worktree, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise MergeError((result.stderr or result.stdout).strip()[-500:] or f"git {args[0]} failed")
    return result


def _sections(text: str, heading: re.Pattern) -> dict[str, str]:
    starts = list(heading.finditer(text or ""))
    return {m[1]: text[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
            for i, m in enumerate(starts)}


def _strip_rule(section: str) -> str:
    return re.sub(r"\n+---\s*$", "", section.rstrip())


def merge_platform_file(path: str, ours: str | None, theirs: str | None, base: str | None = None) -> str | None:
    """Main's version with what this branch changed since the common base put back (its own story's sections and
    links; a section main changed and the branch did not stays main's); None when the file is not one the platform
    writes per story (a code conflict), or when one side deleted it."""
    if ours is None or theirs is None:
        return None
    base = base or ""
    name = path.split("/", 2)[-1] if path.startswith("specs/") and path.count("/") >= 2 else ""
    if name in SECTION_FILES or name == "tasks.md":
        heading = PHASE_HEADING if name == "tasks.md" else US_HEADING
        merged = theirs
        main_sections, base_sections = _sections(theirs, heading), _sections(base, heading)
        for story, section in _sections(ours, heading).items():
            if section.strip() in (main_sections.get(story, "").strip(), base_sections.get(story, "").strip()):
                continue  # the same on main, or not changed on this branch
            if name == "tasks.md":  # the story keeps its phase when main has it, else takes main's next one
                section = re.sub(r"^## Phase \d+:", f"## Phase {phase_number(merged, story)}:", section, count=1)
            merged = upsert_section(merged, "", story, _strip_rule(section), heading=heading)
        return merged
    if name == "sdlc.yaml":
        mine, main = yaml.safe_load(ours) or {}, yaml.safe_load(theirs) or {}
        stories = {**(mine.get("stories") or {}), **(main.get("stories") or {})}
        main["stories"] = dict(sorted(stories.items()))
        for key, value in mine.items():
            main.setdefault(key, value)
        header = theirs.splitlines()[0] + "\n" if theirs.startswith("#") else ""
        return header + yaml.safe_dump(main, sort_keys=False, allow_unicode=True)
    if path in ("SRS.md", "SDS.md"):
        return _put_back_links(ours, theirs, base)
    return None


def _put_back_links(ours: str, theirs: str, base: str) -> str:
    """The `> Spec:` and `> Plan:` lines this branch added under a feature heading, added under the same heading of
    main's version (when main does not have them yet)."""
    lines = ours.splitlines()
    merged = theirs
    heading = ""
    for line in lines:
        if line.startswith("###"):
            heading = line
        elif LINK.match(line) and heading and line not in merged and line not in base.splitlines():
            index = merged.find(heading + "\n")
            if index < 0:
                continue  # main renamed the heading: the link waits for the story's next document commit
            at = index + len(heading)
            merged = merged[:at] + "\n\n" + line + merged[at:]
    return merged


def tc_collisions(path: str, ours: str, merged: str, story: str) -> list[str]:
    """Test case numbers this story uses that another story of main uses too (numbers are file-wide)."""
    mine = set(re.findall(r"\bTC-\d+\b", _sections(ours, US_HEADING).get(story, "")))
    others = {tc for other, text in _sections(merged, US_HEADING).items() if other != story
              for tc in re.findall(r"\bTC-\d+\b", text)}
    return [f"{path}: {tc} is used by another story too" for tc in sorted(mine & others)]


REVISION = re.compile(r"^revision\s*(?::\s*\w+)?\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
DOWN = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*(.+)$", re.MULTILINE)


def _migration_folders(worktree: Path) -> list[Path]:
    return sorted({p.parent for p in worktree.glob("**/versions/*.py")
                   if "node_modules" not in p.parts and ".venv" not in p.parts})


def _revisions(folder: Path) -> dict[str, list[tuple[Path, list[str]]]]:
    """Alembic revision id -> the files defining it, each with its down revisions."""
    revisions: dict[str, list[tuple[Path, list[str]]]] = {}
    for file in sorted(folder.glob("*.py")):
        text = file.read_text(errors="replace")
        found = REVISION.search(text)
        if not found:
            continue
        down = DOWN.search(text)
        revisions.setdefault(found[1], []).append((file, re.findall(r"['\"]([^'\"]+)['\"]", down[1]) if down else []))
    return revisions


def _heads(revisions: dict) -> list[str]:
    parents = {p for defined in revisions.values() for _, downs in defined for p in downs}
    return sorted(r for r in revisions if r not in parents)


def migration_problems(worktree: Path) -> list[str]:
    """Alembic folders with more than one head, or a revision id used twice."""
    problems = []
    for folder in _migration_folders(worktree):
        revisions = _revisions(folder)
        if not revisions:
            continue
        relative = folder.relative_to(worktree)
        for revision, defined in revisions.items():
            if len(defined) > 1:
                problems.append(f"{relative}: revision {revision} is defined in {', '.join(f.name for f, _ in defined)}")
        heads = _heads(revisions)
        if len(heads) > 1:
            problems.append(f"{relative}: {len(heads)} heads ({', '.join(heads)}); the branch's migration must "
                            f"follow main's latest one")
    return problems


def rechain_migrations(worktree: Path, main: str) -> list[str]:
    """After main is merged: when the branch's own migrations and main's end in two heads, the branch's first own
    migration is made to follow main's head (its down_revision and its Revises line), as a person would do after
    another story merged first. Several own chains, a merge revision or a revision id used twice are left alone."""
    own = {Path(f) for f in _git(worktree, "diff", "--name-only", "--diff-filter=A", main, "HEAD").stdout.split()}
    notes = []
    for folder in _migration_folders(worktree):
        revisions = _revisions(folder)
        if any(len(defined) > 1 for defined in revisions.values()):
            continue
        heads = _heads(revisions)
        mine = [h for h in heads if revisions[h][0][0].relative_to(worktree) in own]
        theirs = [h for h in heads if h not in mine]
        if len(heads) != 2 or len(mine) != 1 or len(theirs) != 1:
            continue
        root = mine[0]
        while True:  # down the branch's own chain to its first migration
            downs = revisions[root][0][1]
            if len(downs) != 1 or downs[0] not in revisions:
                break
            if revisions[downs[0]][0][0].relative_to(worktree) not in own:
                break
            root = downs[0]
        file, downs = revisions[root][0]
        if len(downs) != 1:
            continue
        text = file.read_text()
        text = DOWN.sub(f'down_revision = "{theirs[0]}"', text, count=1)
        text = re.sub(r"^Revises:.*$", f"Revises: {theirs[0]}", text, count=1, flags=re.MULTILINE)
        file.write_text(text)
        _git(worktree, "add", "--", str(file.relative_to(worktree)))
        notes.append(f"{file.relative_to(worktree)}: now follows {theirs[0]} (main's latest migration) instead of "
                     f"{downs[0]}")
    return notes


def is_up_to_date(worktree: Path, main: str) -> bool:
    return _git(worktree, "merge-base", "--is-ancestor", main, "HEAD", check=False).returncode == 0


def already_merged(worktree: Path, main: str) -> bool:
    """main already holds the branch: its pull request was merged, there is nothing to bring in."""
    return _git(worktree, "merge-base", "--is-ancestor", "HEAD", main, check=False).returncode == 0


MERGE_SUBJECT = "sdlc: merge "


def only_main_merges(worktree: Path, old: str, new: str) -> bool:
    """Every commit from old to new (first parents) is the platform merging main into the branch: a pull request
    merged at old holds all of this story's own work."""
    result = _git(worktree, "log", "--first-parent", "--format=%s", f"{old}..{new}", check=False)
    subjects = [line for line in result.stdout.splitlines() if line]
    return result.returncode == 0 and bool(subjects) and all(s.startswith(MERGE_SUBJECT) for s in subjects)


def merge_main(worktree: Path, main: str, story: str = "") -> dict:
    """Merge main into the branch and commit. Platform files are merged here; code conflicts are committed with
    their markers for the Developer's task (the merge must be one commit, so main is an ancestor afterwards)."""
    if is_up_to_date(worktree, main):
        return {"up_to_date": True}
    status = _git(worktree, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if status:
        raise MergeError(f"the worktree has uncommitted changes: {status.splitlines()[0]}")
    _git(worktree, "merge", "--no-ff", "--no-commit", main, check=False)
    conflicted = [f for f in _git(worktree, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if f]
    resolved, left, notes = [], [], []
    for path in conflicted:
        base, ours, theirs = (_git(worktree, "show", f":{stage}:{path}", check=False) for stage in (1, 2, 3))
        merged = merge_platform_file(path, ours.stdout if ours.returncode == 0 else None,
                                     theirs.stdout if theirs.returncode == 0 else None,
                                     base.stdout if base.returncode == 0 else "")
        if merged is None:
            left.append(path)
            continue
        (worktree / path).write_text(merged)
        _git(worktree, "add", "--", path)
        resolved.append(path)
        if story and path.endswith("/test_cases.md"):
            notes += tc_collisions(path, ours.stdout, merged, story)
    if left:
        _git(worktree, "add", "--", *left)  # with their markers: the Developer's task resolves them
    notes += rechain_migrations(worktree, main)
    message = (f"{MERGE_SUBJECT}{main[:12]} from the default branch"
               + (f"; {len(left)} conflict(s) left for the Developer" if left else ""))
    _git(worktree, "commit", "--no-verify", "-m", message)
    return {"up_to_date": False, "commit": _git(worktree, "rev-parse", "HEAD").stdout.strip(),
            "resolved": resolved, "conflicts": left, "notes": notes,
            "migrations": migration_problems(worktree)}


def markers_left(worktree: Path, files: list[str]) -> list[str]:
    """Files that still hold conflict markers."""
    found = []
    for path in files:
        file = worktree / path
        if file.is_file() and MARKER.search(file.read_text(errors="replace")):
            found.append(path)
    return found
