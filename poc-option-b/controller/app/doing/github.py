"""Release Engineer: push the run branch, open a draft PR, and read its CI, review and merge state.

Uses the operator's `gh` login as git credential helper for one push, without
changing global git configuration and without putting a token on a command line.
Never merges: a run completes only when GitHub reports a merge of the exact head
the team pushed.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from controller.app.doing.process import run_bounded

GITHUB = re.compile(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?")
GH_TIMEOUT = 60
PUSH_TIMEOUT = 300


class GitHubError(RuntimeError):
    pass


def repo_slug(repo_url: str) -> str | None:
    match = GITHUB.fullmatch(repo_url.strip())
    return match[1] if match else None


def _gh(*args: str, input_text: str | None = None, runner=run_bounded) -> str:
    result = runner(["gh", *args], timeout=GH_TIMEOUT, input_text=input_text)
    if not result.ok:
        raise GitHubError(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: {result.tail(5)}")
    return result.output


@dataclass(frozen=True)
class PullState:
    state: str  # OPEN, CLOSED, MERGED
    head_sha: str
    merged_sha: str | None
    ci_status: str  # success, failure, pending, none
    review_status: str


def check_access(slug: str, runner=run_bounded) -> str:
    """Raises GitHubError unless the operator's login can push to the repository."""
    data = json.loads(_gh("api", f"repos/{slug}", "--jq", "{push: .permissions.push, private: .private}", runner=runner))
    if not data.get("push"):
        raise GitHubError(f"the gh login cannot push to {slug}")
    return f"gh can push to {slug}"


def push(worktree: Path, branch: str, runner=run_bounded) -> None:
    result = runner(["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                     "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=worktree, timeout=PUSH_TIMEOUT,
                    env=os.environ | {"GIT_TERMINAL_PROMPT": "0"})
    if not result.ok:
        raise GitHubError(f"push of {branch} failed: {result.tail(5)}")


def open_draft(slug: str, base: str, branch: str, title: str, body: str, runner=run_bounded) -> tuple[int, str]:
    """Create the draft PR, or update the body of the open PR for this branch (idempotent)."""
    existing = json.loads(_gh("pr", "list", "--repo", slug, "--head", branch, "--state", "open",
                              "--json", "number,url", runner=runner) or "[]")
    if existing:
        number, url = existing[0]["number"], existing[0]["url"]
        # REST, not `gh pr edit`: older gh versions query the retired classic Projects API there and fail.
        _gh("api", "--method", "PATCH", f"repos/{slug}/pulls/{number}", "--input", "-",
            input_text=json.dumps({"body": body}), runner=runner)
        return number, url
    url = _gh("pr", "create", "--repo", slug, "--draft", "--base", base, "--head", branch, "--title", title,
              "--body-file", "-", input_text=body, runner=runner).strip().splitlines()[-1]
    return int(url.rstrip("/").rsplit("/", 1)[-1]), url


def _ci(checks: list[dict]) -> str:
    if not checks:
        return "none"
    states = {(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in checks}
    if states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
        return "failure"
    if states <= {"SUCCESS", "NEUTRAL", "SKIPPED"}:
        return "success"
    return "pending"


def pull_state(slug: str, number: int, runner=run_bounded) -> PullState:
    data = json.loads(_gh("pr", "view", str(number), "--repo", slug, "--json",
                          "state,headRefOid,mergeCommit,statusCheckRollup,reviewDecision", runner=runner))
    merge = data.get("mergeCommit") or {}
    return PullState(data["state"], data["headRefOid"], merge.get("oid"), _ci(data.get("statusCheckRollup") or []),
                     (data.get("reviewDecision") or "pending").lower())
