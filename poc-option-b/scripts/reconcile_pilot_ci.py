"""Audit a completed GitHub Actions check for the isolated pilot PR."""

import argparse
import json
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.config import get_settings
from controller.app.db import get_engine
from controller.app.models import Execution, PullRequest, Run, Task
from controller.app.state_machine import event
from scripts.pilot_branches import feature_id_for_requirement, pr_base_branch


MAX_GH_BYTES = 1_000_000


def gh_json(*args: str) -> dict:
    completed = subprocess.run(
        ["gh", *args], capture_output=True, timeout=30, check=False,
    )
    if completed.returncode or len(completed.stdout) > MAX_GH_BYTES:
        raise ValueError("GitHub read failed or exceeded the response limit")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("GitHub response is not an object")
    return value


def verify_checks(payload: dict, *, name: str, sha: str) -> list[int]:
    checks = payload.get("check_runs")
    if (not isinstance(checks, list) or not checks or payload.get("total_count") != len(checks)
            or len(checks) > 100):
        raise ValueError("required check list is empty or incomplete")
    ids = []
    for check in checks:
        if (not isinstance(check, dict) or check.get("name") != name
                or check.get("head_sha") != sha or check.get("status") != "completed"
                or check.get("conclusion") != "success"
                or not isinstance(check.get("app"), dict)
                or check["app"].get("slug") != "github-actions"
                or not isinstance(check.get("id"), int)):
            raise ValueError("required GitHub Actions check has not passed")
        ids.append(check["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate GitHub check IDs")
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to record a verified CI result")
    settings = get_settings()
    with Session(get_engine()) as session:
        run = session.get(Run, args.run_id)
        pr = session.scalar(select(PullRequest).where(PullRequest.run_id == args.run_id))
        if (run is None or pr is None or run.status != "WAITING_PR_APPROVAL"
                or pr.ci_status != "pending" or run.repo_id not in settings.repo_allowlist
                or pr.repo_full_name != run.repo_id):
            raise SystemExit("pilot PR is not awaiting CI reconciliation")
        repo, number, sha = pr.repo_full_name, pr.number, pr.head_sha
        requirement = run.requirement
        expected_branch = session.scalar(
            select(Execution.branch).join(Task, Execution.task_id == Task.id)
            .where(Task.run_id == args.run_id, Execution.status == "COMPLETED")
            .limit(1)
        )
    manifest = json.loads((Path(__file__).resolve().parents[1]
                           / "fixtures/task-board/benchmark.json").read_text())
    if (repo != manifest["repository"].removeprefix("https://github.com/").removesuffix(".git")
            or not expected_branch):
        raise SystemExit("PR does not match the pinned pilot")
    project_root = Path(__file__).resolve().parents[1]
    try:
        feature_id = feature_id_for_requirement(manifest, requirement, project_root)
        base_branch = pr_base_branch(manifest, feature_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    details = gh_json(
        "pr", "view", str(number), "--repo", repo, "--json",
        "number,url,isDraft,baseRefName,headRefName,headRefOid",
    )
    if (details.get("number") != number or details.get("url") != f"https://github.com/{repo}/pull/{number}"
            or details.get("isDraft") is not True or details.get("baseRefName") != base_branch
            or details.get("headRefName") != expected_branch or details.get("headRefOid") != sha):
        raise SystemExit("GitHub PR identity changed; CI reconciliation refused")
    checks = gh_json(
        "api", f"repos/{repo}/commits/{sha}/check-runs?check_name={settings.github_required_check}&filter=all&per_page=100",
    )
    check_ids = verify_checks(checks, name=settings.github_required_check, sha=sha)
    with Session(get_engine()) as session:
        pr = session.scalar(select(PullRequest).where(PullRequest.run_id == args.run_id).with_for_update())
        run = session.get(Run, args.run_id)
        if (run.status != "WAITING_PR_APPROVAL" or pr.ci_status != "pending"
                or (pr.repo_full_name, pr.number, pr.head_sha) != (repo, number, sha)):
            raise SystemExit("PR changed during CI reconciliation")
        pr.ci_status = "success"
        event(session, args.run_id, "ci.reconciled", {
            "actor": "codex-operator", "source": "github-checks-api",
            "check": settings.github_required_check, "check_run_ids": check_ids,
            "head_sha": sha, "status": "success",
        })
        session.commit()
    print(json.dumps({"run_id": str(args.run_id), "ci_status": "success", "check_run_ids": check_ids}))


if __name__ == "__main__":
    main()
