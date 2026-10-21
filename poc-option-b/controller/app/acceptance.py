"""Acceptance: the user story tried on its running preview by end-to-end tests, with evidence per criterion.

The QA Engineer writes Playwright tests whose titles start with the criterion id (AC-01, EC-02); the platform runs
them in the project's e2e image inside the preview's network and reads the JSON report they write to /evidence.
Screenshots and videos stay under .data/evidence/<run>/<attempt>/ for the QC's acceptance gate.
"""

import json
from pathlib import Path
import re
import uuid

from controller.app.doing.process import CONTAINER_PREFIX, run_bounded
from controller.app.preview import network
from controller.app.projects.config import Preview

CRITERION = re.compile(r"\b((?:AC|EC)-\d+)\b")
REPORT = "results.json"


def run_e2e(run_id: str, worktree: Path, preview: Preview, evidence: Path, memory_mb: int) -> dict:
    """Run the acceptance tests next to the preview; returns ok, detail and the parsed report (or None)."""
    evidence.mkdir(parents=True, exist_ok=True)
    name = f"{CONTAINER_PREFIX}e2e-{uuid.uuid4().hex[:12]}"
    result = run_bounded(["docker", "run", "--rm", "--name", name, "--network", network(run_id),
                          "--memory", f"{memory_mb}m", "--security-opt", "no-new-privileges", "--ipc", "host",
                          "-v", f"{worktree}:/work:ro", "-v", f"{evidence}:/evidence", "-w", "/work",
                          "-e", f"BASE_URL=http://{preview.service}:{preview.port}", "-e", "CI=1", "-e", "HOME=/tmp",
                          "-e", f"PLAYWRIGHT_JSON_OUTPUT_NAME=/evidence/{REPORT}",
                          preview.e2e_image, *preview.e2e], timeout=preview.e2e_timeout_seconds, container=name)
    if result.timed_out:
        run_bounded(["docker", "rm", "-f", name], timeout=30)
    (evidence / "e2e.log").write_text(result.output)
    report_path = evidence / REPORT
    if not report_path.is_file():
        return {"ok": False, "report": None,
                "detail": f"no {REPORT} in /evidence (exit {result.exit_code}{', timed out' if result.timed_out else ''}): "
                          f"{result.tail(30)}"}
    try:
        report = json.loads(report_path.read_text())
    except ValueError as exc:
        return {"ok": False, "report": None, "detail": f"{REPORT} is not JSON: {exc}"}
    return {"ok": True, "report": report, "detail": f"e2e exit {result.exit_code}"}


def _specs(suite: dict):
    for spec in suite.get("specs", []):
        yield spec
    for child in suite.get("suites", []):
        yield from _specs(child)


def parse_report(report: dict, evidence: Path) -> list[dict]:
    """One case per test of the Playwright JSON report: its criteria, outcome, error and evidence files (relative to
    the evidence folder)."""
    cases = []
    for suite in report.get("suites", []):
        for spec in _specs(suite):
            for test in spec.get("tests", []):
                results = test.get("results") or [{}]
                last = results[-1]
                files = []
                for attachment in last.get("attachments", []):
                    path = str(attachment.get("path") or "")
                    relative = path.removeprefix("/evidence/") if path.startswith("/evidence/") else ""
                    if relative and (evidence / relative).is_file():
                        files.append({"name": attachment.get("name", ""), "type": attachment.get("contentType", ""),
                                      "path": relative})
                error = (last.get("error") or {}).get("message", "")
                cases.append({"title": spec.get("title", ""), "criteria": CRITERION.findall(spec.get("title", "")),
                              "status": last.get("status", test.get("status", "unknown")),
                              "error": re.sub(r"\x1b\[[0-9;]*m", "", error)[:1500], "attachments": files})
    return cases


def coverage(criteria: list[str], cases: list[dict]) -> list[str]:
    """Each criterion of the story has a test, and every test of it passed."""
    problems = []
    for criterion in criteria:
        mine = [c for c in cases if criterion in c["criteria"]]
        if not mine:
            problems.append(f"{criterion} has no acceptance test")
        elif any(c["status"] != "passed" for c in mine):
            problems.append(f"{criterion} failed: " + "; ".join(c["title"] for c in mine if c["status"] != "passed"))
    return problems


def render_acceptance(title: str, criteria: dict[str, str], cases: list[dict], url: str, evidence_base: str) -> str:
    """The acceptance report: one row per criterion with its tests, outcome and evidence links."""
    def cell(text: str) -> str:
        return " ".join(str(text).split()).replace("|", "\\|")
    out = [f"# Acceptance: {title}", "", f"Preview: {url or 'not running'}", "",
           "| Criterion | Test | Result | Evidence |", "|---|---|---|---|"]
    for cid, text in criteria.items():
        mine = [c for c in cases if cid in c["criteria"]] or [{"title": "—", "status": "missing", "attachments": []}]
        for case in mine:
            links = ", ".join(f"[{a['name'] or Path(a['path']).name}]({evidence_base}/{a['path']})" for a in case["attachments"])
            out.append(f"| {cid}: {cell(text)[:120]} | {cell(case['title'])} | {case['status']} | {links or '—'} |")
    failed = [c for c in cases if c["status"] != "passed" and c.get("error")]
    if failed:
        out += ["", "## Failures", ""]
        for case in failed:
            out += [f"### {case['title']}", "", "```", case["error"], "```", ""]
    other = [c for c in cases if not c["criteria"]]
    if other:
        out += ["", f"Tests without a criterion id in their title: {', '.join(c['title'] for c in other)}", ""]
    return "\n".join(out)
