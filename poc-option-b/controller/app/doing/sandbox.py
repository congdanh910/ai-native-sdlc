"""What an AI CLI session's commands may read and write: the run's worktree and the installed tools, nothing else.

Codex runs its commands under a permission profile (`default_permissions` with `[permissions.<name>]`): the
platform's minimal read set, the worktree (writable, or read-only for the thinking roles), the temporary folders, the
install prefixes of the tools on PATH, the git directory the worktree belongs to, and SDLC_EXECUTOR_READ_PATHS.
Other folders of the operator's home, such as other projects, cannot be read. No network.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess

from controller.app.config import get_settings

PROFILE = "sdlc"


def tool_roots(path_env: str, home: Path) -> list[Path]:
    """The install prefix of each folder on PATH (~/.nvm/versions/node/v20/bin gives ~/.nvm/versions/node/v20, so
    npm's own files are readable), never the home folder or / itself."""
    roots: list[Path] = []
    for entry in path_env.split(os.pathsep):
        folder = Path(entry).expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            continue
        folder = folder.resolve()
        root = folder.parent if folder.name in ("bin", "sbin") else folder
        if root in (home.resolve(), Path("/")):
            root = folder
        if root not in roots:
            roots.append(root)
    return roots


def git_dir(worktree: Path) -> Path | None:
    """The repository a git worktree belongs to (its .git file points there); git status needs to read it."""
    try:
        out = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    return path if path and not path.resolve().is_relative_to(worktree.resolve()) else None


def _prefix(path: Path) -> list[Path]:
    """The install prefix of an executable, and the folders holding a linked folder on its way (the link itself must
    be readable: standalone/current -> releases/0.162.0)."""
    folder = path.parent
    return [folder.parent if folder.name == "bin" else folder] + [a.parent for a in (folder, *folder.parents)
                                                                   if a.is_symlink()]


def cli_roots(binary: str) -> list[Path]:
    """The folders of the CLI itself, through each link to its release: Codex runs itself as a sandboxed helper and
    puts the tools it bundles (rg) on its commands' PATH from there."""
    found = shutil.which(binary)
    if not found:
        return []
    roots, path = [], Path(found)
    for _ in range(10):
        roots += _prefix(path)
        if not path.is_symlink():
            break
        target = Path(os.readlink(path))
        path = target if target.is_absolute() else path.parent / target
    return list(dict.fromkeys(roots + _prefix(Path(found).resolve())))


def readable(worktree: Path) -> list[Path]:
    extra = [Path(p.strip()).expanduser() for p in get_settings().sdlc_executor_read_paths.split(",") if p.strip()]
    paths = tool_roots(os.environ.get("PATH", ""), Path.home()) + [p for p in extra if p.exists()]
    paths += [root for root in cli_roots("codex") if root not in (Path.home(), Path.home().resolve(), Path("/"))]
    repository = git_dir(worktree)
    if repository:
        paths.append(repository)
    inside = worktree.resolve()
    return [p for p in dict.fromkeys(paths) if not p.resolve().is_relative_to(inside)]


def codex_args(worktree: Path, *, write: bool) -> list[str]:
    """The -c overrides that select the profile; the older --sandbox flag must not be passed with them."""
    filesystem = {":minimal": "read", ":tmpdir": "write", ":slash_tmp": "write",
                  ":workspace_roots": {".": "write" if write else "read"},
                  **{str(path): "read" for path in readable(worktree)}}
    table = ", ".join(f"{json.dumps(key)} = {_toml(value)}" for key, value in filesystem.items())
    return ["-c", f'default_permissions="{PROFILE}"', "-c", f"permissions.{PROFILE}.filesystem={{{table}}}",
            "-c", f"permissions.{PROFILE}.network.enabled=false"]


def _toml(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)
