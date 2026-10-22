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
IMAGE_VERSION = re.compile(r":v?(\d+\.\d+\.\d+)")
# The Playwright image has the browsers but not the @playwright/test package the tests import. The repository is
# mounted read-only (the tests cannot change what they test), so the run works on a copy without dependency folders:
# the e2e folder's own lockfile when it has one, otherwise the package at the image's version, linked in.
PREPARE = """set -e
mkdir -p /tmp/work
(cd /work && tar --exclude=node_modules --exclude=.venv --exclude=.git -cf - .) | tar -xf - -C /tmp/work
cd /tmp/work
if [ -f {folder}/package-lock.json ]; then
  npm ci --prefix {folder} --no-audit --no-fund
elif [ -n "{version}" ] && [ ! -d node_modules/@playwright/test ]; then
  npm install --no-save --no-audit --no-fund --prefix /tmp/pw @playwright/test@{version}
  ln -s /tmp/pw/node_modules /tmp/work/node_modules
fi
exec "$@"
"""


def e2e_argv(preview: Preview) -> list[str]:
    """The acceptance command, run after preparing a writable copy of the repository with Playwright installed."""
    match = IMAGE_VERSION.search(preview.e2e_image)
    folder = (preview.e2e_paths[0].split("/")[0] if preview.e2e_paths else "e2e") or "e2e"
    return ["sh", "-c", PREPARE.format(folder=folder, version=match[1] if match else ""), "e2e", *preview.e2e]


def run_e2e(run_id: str, worktree: Path, preview: Preview, evidence: Path, memory_mb: int) -> dict:
    """Run the acceptance tests next to the preview; returns ok, detail and the parsed report (or None)."""
    evidence.mkdir(parents=True, exist_ok=True)
    name = f"{CONTAINER_PREFIX}e2e-{uuid.uuid4().hex[:12]}"
    result = run_bounded(["docker", "run", "--rm", "--name", name, "--network", network(run_id),
                          "--memory", f"{memory_mb}m", "--security-opt", "no-new-privileges", "--ipc", "host",
                          "-v", f"{worktree}:/work:ro", "-v", f"{evidence}:/evidence", "-w", "/work",
                          "-e", f"BASE_URL=http://{preview.service}:{preview.port}", "-e", "CI=1", "-e", "HOME=/tmp",
                          "-e", f"PLAYWRIGHT_JSON_OUTPUT_NAME=/evidence/{REPORT}",
                          preview.e2e_image, *e2e_argv(preview)], timeout=preview.e2e_timeout_seconds, container=name)
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


# Failures of the test code itself, not of the application: an ambiguous locator, code that does not load.
AUTHORING = re.compile(r"strict mode violation|SyntaxError|ReferenceError|Cannot find module|is not defined|"
                       r"is not a function|Unexpected token|TS\d{4}:")


def authoring_errors(cases: list[dict]) -> list[str]:
    """The failed tests whose error is in the test code, each with the first lines of its error."""
    return [f"{c['title']}: " + " ".join(c["error"].split())[:400]
            for c in cases if c["status"] != "passed" and AUTHORING.search(c.get("error") or "")]


def coverage(criteria: list[str], cases: list[dict], proven: dict[str, list[str]] | None = None) -> list[str]:
    """Each criterion of the story has a test, and every test of it passed. A criterion a browser cannot show (a
    restart, a clock, a test double's failure) needs no browser test when passing tests of the coding prove it."""
    problems = []
    for criterion in criteria:
        mine = [c for c in cases if criterion in c["criteria"]]
        if not mine and not (proven or {}).get(criterion):
            problems.append(f"{criterion} has no acceptance test and no passing test of the coding")
        elif any(c["status"] != "passed" for c in mine):
            problems.append(f"{criterion} failed: " + "; ".join(c["title"] for c in mine if c["status"] != "passed"))
    return problems


def render_acceptance(title: str, criteria: dict[str, str], cases: list[dict], url: str, evidence_base: str,
                      proven: dict[str, list[str]] | None = None) -> str:
    """The acceptance report: one row per criterion with its tests, outcome and evidence links."""
    def cell(text: str) -> str:
        return " ".join(str(text).split()).replace("|", "\\|")
    out = [f"# Acceptance: {title}", "", f"Preview: {url or 'not running'}", "",
           "| Criterion | Test | Result | Evidence |", "|---|---|---|---|"]
    for cid, text in criteria.items():
        elsewhere = (proven or {}).get(cid)
        missing = {"title": f"— (proven by the coding's tests {', '.join(elsewhere)})" if elsewhere else "—",
                   "status": "proven by tests" if elsewhere else "missing", "attachments": []}
        mine = [c for c in cases if cid in c["criteria"]] or [missing]
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
