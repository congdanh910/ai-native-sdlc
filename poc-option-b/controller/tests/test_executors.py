import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from controller.app.executors.claude import run_claude
from controller.app.executors.codex import run_codex
from controller.app.executors.git_workspace import WorkspaceError, create_worktree
from controller.app.executors.process import run_process
from controller.app.executors.validation import ValidationError, changed_paths, run_validation


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture
def pilot(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "pilot"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("const value = 1;\n")
    (repo / "AGENTS.md").write_text("Pinned agent context\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    return repo, git(repo, "rev-parse", "HEAD")


def test_worktree_is_pinned_and_separate(pilot, tmp_path):
    repo, commit = pilot
    workspace = asyncio.run(create_worktree(
        repo, allowlisted_repo=repo, worktree_root=tmp_path / "worktrees",
        base_commit=commit, task_slug="search", executor="codex",
    ))
    assert workspace.path != repo
    assert git(workspace.path, "rev-parse", "HEAD") == commit
    assert git(workspace.path, "branch", "--show-current") == workspace.branch
    assert (workspace.path / "AGENTS.md").read_text() == "Pinned agent context\n"


def test_repo_allowlist_rejects_other_root(pilot, tmp_path):
    repo, commit = pilot
    with pytest.raises(WorkspaceError, match="allowlisted"):
        asyncio.run(create_worktree(
            repo, allowlisted_repo=tmp_path, worktree_root=tmp_path / "worktrees",
            base_commit=commit, task_slug="search", executor="claude",
        ))


def test_validation_blocks_protected_and_symlink_paths(pilot, tmp_path):
    repo, commit = pilot
    workspace = asyncio.run(create_worktree(
        repo, allowlisted_repo=repo, worktree_root=tmp_path / "worktrees",
        base_commit=commit, task_slug="search", executor="codex",
    ))
    (workspace.path / "src" / "app.js").write_text("const value = 2;\n")
    assert asyncio.run(changed_paths(workspace.path, commit, ["src"])) == ["src/app.js"]
    (workspace.path / ".env.local").write_text("SECRET=x\n")
    with pytest.raises(ValidationError, match="protected"):
        asyncio.run(changed_paths(workspace.path, commit, ["src", ".env.local"]))
    (workspace.path / ".env.local").unlink()
    (workspace.path / "src" / "link").symlink_to(tmp_path)
    with pytest.raises(ValidationError, match="symlink"):
        asyncio.run(changed_paths(workspace.path, commit, ["src"]))


def test_ignored_protected_file_is_detected(pilot, tmp_path):
    repo, commit = pilot
    (repo / ".git" / "info" / "exclude").write_text(".env.*\n")
    workspace = asyncio.run(create_worktree(
        repo, allowlisted_repo=repo, worktree_root=tmp_path / "worktrees",
        base_commit=commit, task_slug="search", executor="codex",
    ))
    (workspace.path / ".env.secret").write_text("key=value\n")
    with pytest.raises(ValidationError, match="protected"):
        asyncio.run(changed_paths(workspace.path, commit, [".env.secret"]))


def test_validation_runs_only_allowlisted_commands(pilot, tmp_path):
    repo, commit = pilot
    workspace = asyncio.run(create_worktree(
        repo, allowlisted_repo=repo, worktree_root=tmp_path / "worktrees",
        base_commit=commit, task_slug="search", executor="codex",
    ))
    commands = ["node --check src/app.js"]
    results = asyncio.run(run_validation(
        workspace.path, commit, ["src"], commands, command_allowlist=commands,
    ))
    assert len(results) == 1 and results[0][1].returncode == 0
    with pytest.raises(ValidationError, match="not allowlisted"):
        asyncio.run(run_validation(
            workspace.path, commit, ["src"], ["node -e console.log(1)"],
            command_allowlist=commands,
        ))


def test_process_timeout_kills_child_group(tmp_path):
    script = tmp_path / "hang.py"
    marker = tmp_path / "child-finished"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import time, pathlib, sys; time.sleep(1); pathlib.Path(sys.argv[1]).write_text(\"done\")', "
        "sys.argv[1]])\n"
        "time.sleep(5)\n"
    )
    with pytest.raises(TimeoutError):
        asyncio.run(run_process(
            ["python3", str(script), str(marker)], tmp_path, timeout_seconds=0.1,
        ))
    import time
    time.sleep(1.2)
    assert not marker.exists()


def test_process_timeout_when_child_holds_output_open(tmp_path):
    script = tmp_path / "parent.py"
    marker = tmp_path / "child-finished"
    script.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import time, pathlib, sys; time.sleep(1); pathlib.Path(sys.argv[1]).write_text(\"done\")', "
        "sys.argv[1]])\n"
    )
    with pytest.raises(TimeoutError):
        asyncio.run(run_process(
            ["python3", str(script), str(marker)], tmp_path, timeout_seconds=0.1,
        ))
    import time
    time.sleep(1.2)
    assert not marker.exists()


def test_process_cancellation_kills_child_group(tmp_path):
    script = tmp_path / "cancel.py"
    marker = tmp_path / "child-finished"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import time, pathlib, sys; time.sleep(1); pathlib.Path(sys.argv[1]).write_text(\"done\")', "
        "sys.argv[1]])\n"
        "time.sleep(5)\n"
    )

    async def scenario():
        process = asyncio.create_task(run_process(
            ["python3", str(script), str(marker)], tmp_path, timeout_seconds=10,
        ))
        await asyncio.sleep(0.1)
        process.cancel()
        with pytest.raises(asyncio.CancelledError):
            await process

    asyncio.run(scenario())
    import time
    time.sleep(1.2)
    assert not marker.exists()


def test_cli_adapters_pass_prompt_and_restrict_tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("codex", "claude"):
        executable = bin_dir / name
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'prompt': sys.stdin.read()}))\n"
        )
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    codex = asyncio.run(run_codex(tmp_path, "Implement search"))
    claude = asyncio.run(run_claude(tmp_path, "Implement search"))
    import json
    c = json.loads(codex.stdout)
    a = json.loads(claude.stdout)
    assert c["cwd"] == a["cwd"] == str(tmp_path)
    assert c["prompt"] == a["prompt"] == "Implement search"
    assert "workspace-write" in c["argv"]
    assert c["argv"][c["argv"].index("-a") + 1] == "never"
    assert "sandbox_workspace_write.network_access=false" in c["argv"]
    assert "web_search=disabled" in c["argv"]
    assert "--ignore-user-config" in c["argv"] and "--ephemeral" in c["argv"]
    assert "--restricted" in a["argv"] and "acceptEdits" in a["argv"]
