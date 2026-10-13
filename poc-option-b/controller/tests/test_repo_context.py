import hashlib
import subprocess

import pytest

from controller.app.repo_context import RepoContextError, load_agent_context, load_pinned_file_list


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_context_comes_from_pinned_commit_not_working_tree(tmp_path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    context_file = tmp_path / "AGENTS.md"
    context_file.write_text("Pinned project instructions\n")
    git(tmp_path, "add", "AGENTS.md")
    git(tmp_path, "commit", "-m", "Add context")
    commit = git(tmp_path, "rev-parse", "HEAD")
    expected = hashlib.sha256(b"Pinned project instructions\n").hexdigest()
    context_file.write_text("Uncommitted change\n")

    context = load_agent_context(tmp_path, commit, expected)
    assert context.text == "Pinned project instructions\n"
    assert context.artifact_path == f"git:{commit}:AGENTS.md"
    with pytest.raises(RepoContextError, match="checksum"):
        load_agent_context(tmp_path, commit, "0" * 64)
    with pytest.raises(RepoContextError, match="full Git hash"):
        load_agent_context(tmp_path, "HEAD")
    (tmp_path / "untracked.txt").write_text("Working tree only")
    assert load_pinned_file_list(tmp_path, commit) == "AGENTS.md"
