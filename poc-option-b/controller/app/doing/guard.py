"""Claude Code PreToolUse hook: block file edits outside the role's writable paths, and reads outside the worktree.

Runs as `python -I guard.py` with only the standard library. The controller
passes the worktree root and the path patterns in environment variables.
Exit code 2 blocks the tool call and shows the message to the model.
"""

from __future__ import annotations  # the hook may run on an older system Python

import json
import os
from pathlib import Path
import re
import sys


def glob_to_regex(pattern: str) -> re.Pattern[str]:  # same rules as projects.config.glob_to_regex
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


READ_TOOLS = ("Read", "Glob", "Grep")


def _inside(target: str, event: dict, root: Path) -> str | None:
    """The path relative to the worktree, or None when it is outside."""
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = Path(event.get("cwd") or root) / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def decide(event: dict, root: Path, allow: list[str], deny: list[str]) -> str | None:
    """The reason to block, or None to let the tool run."""
    tool_input = event.get("tool_input") or {}
    tool = event.get("tool_name")
    if tool in READ_TOOLS:  # other projects and the operator's files stay unread
        targets = [tool_input.get("file_path"), tool_input.get("path")]
        pattern = tool_input.get("pattern") or ""
        if tool == "Glob" and pattern.startswith(("/", "~")):  # Grep's pattern is a regular expression, not a path
            targets.append(pattern)
        for target in filter(None, targets):
            if _inside(target, event, root) is None:
                return f"{target} is outside the repository worktree"
        return None
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return None
    relative = _inside(target, event, root)
    if relative is None:
        return f"{target} is outside the repository worktree"
    if matches(relative, deny):
        return f"{relative} is protected for this role"
    if not matches(relative, allow):
        return f"{relative} is not one of the files this role may change ({', '.join(allow)})"
    return None


def main() -> int:
    event = json.load(sys.stdin)
    reason = decide(event, Path(os.environ["SDLC_GUARD_ROOT"]), json.loads(os.environ["SDLC_GUARD_ALLOW"]),
                    json.loads(os.environ.get("SDLC_GUARD_DENY", "[]")))
    if reason:
        print(f"Blocked by the SDLC controller: {reason}.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
