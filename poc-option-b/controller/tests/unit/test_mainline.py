"""Merging main into a run's branch: the platform's own files merge without a model, code conflicts and migration
heads are left for the Developer."""

from pathlib import Path
import subprocess

from controller.app.mainline import markers_left, merge_main, merge_platform_file, migration_problems

HEADER = "# Feature Specification: Employees\n\n---\n\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def section(story: str, text: str) -> str:
    return f"## {story}: Title\n\n{text}\n\n---\n"


def test_each_story_keeps_its_own_section_of_a_shared_spec(tmp_path):
    base = HEADER + section("EMP-US-01", "one")
    ours = base + "\n" + section("EMP-US-02", "two")
    theirs = base.replace("one", "one, revised on main") + "\n" + section("EMP-US-03", "three")
    merged = merge_platform_file("specs/001-employees/spec.md", ours, theirs, base)
    assert "one, revised on main" in merged  # main changed it, the branch did not
    assert merged.index("EMP-US-03") < merged.index("EMP-US-02") and "two" in merged and "three" in merged


def test_links_and_feature_context_merge_and_code_does_not():
    base = "### 7.1 Employees (EMP)\n\nText\n"
    ours = "### 7.1 Employees (EMP)\n\n> Spec: [specs/001/spec.md](specs/001/spec.md)\n\nText\n"
    theirs = "### 7.1 Employees (EMP)\n\nText, edited\n\n### 7.2 Shifts (SHF)\n\n> Spec: [specs/002/spec.md](specs/002/spec.md)\n"
    merged = merge_platform_file("SRS.md", ours, theirs, base)
    assert merged.startswith("### 7.1 Employees (EMP)\n\n> Spec: [specs/001/spec.md]") and "Text, edited" in merged
    yaml_merged = merge_platform_file("specs/001/sdlc.yaml", "# c\nfeature_id: '001'\nstories:\n  EMP-US-02: {title: B}\n",
                                      "# c\nfeature_id: '001'\nstories:\n  EMP-US-03: {title: C}\n", "")
    assert "EMP-US-02" in yaml_merged and "EMP-US-03" in yaml_merged
    assert merge_platform_file("backend/app/main.py", "a", "b", "") is None
    assert merge_platform_file("specs/001/spec.md", None, "b", "") is None


def test_two_migrations_on_the_same_head_are_found(tmp_path):
    versions = tmp_path / "backend" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0001_a.py").write_text('revision = "0001"\ndown_revision = None\n')
    (versions / "0002_b.py").write_text('revision = "0002"\ndown_revision = "0001"\n')
    assert migration_problems(tmp_path) == []
    (versions / "0002_c.py").write_text('revision: str = "0002c"\ndown_revision: str | None = "0001"\n')
    assert migration_problems(tmp_path) == [
        "backend/migrations/versions: 2 heads (0002, 0002c); the branch's migration must follow main's latest one"]


def test_main_merges_into_the_branch_with_code_conflicts_left_for_the_developer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    spec = "specs/001-employees/spec.md"
    commit(repo, {spec: HEADER + section("EMP-US-01", "one"), "app.py": "x = 1\n"}, "base")
    git(repo, "checkout", "--quiet", "-b", "run")
    commit(repo, {spec: HEADER + section("EMP-US-01", "one") + "\n" + section("EMP-US-02", "two"), "app.py": "x = 2\n"}, "run")
    git(repo, "checkout", "--quiet", "main")
    main = commit(repo, {spec: HEADER + section("EMP-US-01", "one") + "\n" + section("EMP-US-03", "three"),
                         "app.py": "x = 3\n"}, "other story")
    git(repo, "checkout", "--quiet", "run")
    result = merge_main(repo, main, story="EMP-US-02")
    assert result["resolved"] == [spec] and result["conflicts"] == ["app.py"] and result["migrations"] == []
    assert "EMP-US-02" in (repo / spec).read_text() and "EMP-US-03" in (repo / spec).read_text()
    assert markers_left(repo, ["app.py", spec]) == ["app.py"]
    assert git(repo, "merge-base", "--is-ancestor", main, "HEAD") == ""  # one merge commit: main is an ancestor
    assert merge_main(repo, main) == {"up_to_date": True}


def test_a_branch_whose_pull_request_was_merged_is_not_updated_and_its_merge_is_recognised(tmp_path):
    from controller.app.mainline import already_merged, only_main_merges
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    commit(repo, {"app.py": "x = 1\n"}, "base")
    git(repo, "checkout", "--quiet", "-b", "run")
    pushed = commit(repo, {"app.py": "x = 2\n"}, "feat(T1): story")
    git(repo, "checkout", "--quiet", "main")
    git(repo, "merge", "--quiet", "--no-ff", "-m", "Merge pull request #16", "run")  # the Code Owner merges
    main = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "run")
    assert already_merged(repo, main)  # update_branch, queued just before the merge, does nothing
    synced = merge_main(repo, main)["commit"]  # what the old job did: merge this very PR back into the branch
    assert only_main_merges(repo, pushed, synced)  # merged at pushed: the run completes
    later = commit(repo, {"app.py": "x = 3\n"}, "feat(T2): more")
    assert not only_main_merges(repo, pushed, later)  # work after the merged head: not all of it was merged


def test_the_branch_migration_is_made_to_follow_main_after_another_story_merged_first(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    v = "backend/migrations/versions/"
    commit(repo, {v + "0006_e.py": '"""E\n\nRevision ID: 0006_e\nRevises: 0005\n"""\nrevision = "0006_e"\ndown_revision = None\n'}, "base")
    git(repo, "checkout", "--quiet", "-b", "run")  # this story: 0007 and 0007b on 0006_e
    commit(repo, {v + "0007_sites.py": '"""Sites\n\nRevision ID: 0007_sites\nRevises: 0006_e\n"""\n'
                                      'revision = "0007_sites"\ndown_revision = "0006_e"\n',
                  v + "0007b_more.py": 'revision = "0007b_more"\ndown_revision = "0007_sites"\n'}, "run")
    git(repo, "checkout", "--quiet", "main")  # another story merged 0008 and 0009 on 0006_e first
    main = commit(repo, {v + "0008_m.py": 'revision = "0008_m"\ndown_revision = "0006_e"\n',
                         v + "0009_login.py": 'revision = "0009_login"\ndown_revision = "0008_m"\n'}, "other story")
    git(repo, "checkout", "--quiet", "run")
    result = merge_main(repo, main)
    assert result["migrations"] == []
    assert result["notes"] == [f"{v}0007_sites.py: now follows 0009_login (main's latest migration) instead of 0006_e"]
    text = (repo / v / "0007_sites.py").read_text()
    assert 'down_revision = "0009_login"' in text and "Revises: 0009_login" in text
    assert git(repo, "status", "--porcelain") == ""  # part of the merge commit
