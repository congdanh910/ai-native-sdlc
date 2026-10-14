"""Validation Gate: allowlisted commands in the project's pinned toolchain container.

Validation runs without network, with the worktree mounted read-only. Setup
commands (for example installing dependencies) run once per worktree with
network and a writable mount, before the first validation. No LLM is involved.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import uuid

from controller.app.doing.process import CONTAINER_PREFIX, ProcessResult, run_bounded
from controller.app.projects.config import EffectivePolicy

SETUP_TIMEOUT = 900
CONTAINER_USER = "1000:1000"


@dataclass(frozen=True)
class CommandReport:
    name: str
    ok: bool
    exit_code: int | None
    seconds: float
    timed_out: bool
    log_path: str
    tail: str


def container_args(image: str, worktree: Path, argv: list[str], *, name: str, network: bool = False,
                   writable: bool = False) -> list[str]:
    return ["docker", "run", "--rm", "--name", name, "--network", "bridge" if network else "none",
            "--cpus", "2", "--memory", "2g", "--pids-limit", "512", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", CONTAINER_USER,
            "--tmpfs", "/tmp:rw,size=256m", "-e", "HOME=/tmp", "-e", "CI=1",
            "-v", f"{worktree}:/work:{'rw' if writable else 'ro'}", "-w", "/work", image, *argv]


def run_in_container(image: str, worktree: Path, argv: list[str], timeout: float, *, network: bool = False,
                     writable: bool = False) -> ProcessResult:
    name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    result = run_bounded(container_args(image, worktree, argv, name=name, network=network, writable=writable),
                         timeout=timeout, container=name)
    if result.timed_out:  # killing the docker client does not stop the container
        run_bounded(["docker", "rm", "-f", name], timeout=30)
    return result


class ValidationGate:
    """Runs the project's validation commands for one run worktree and keeps their logs as files."""

    def __init__(self, policy: EffectivePolicy, worktree: Path, log_dir: Path, runner=run_in_container):
        self.policy, self.worktree, self.log_dir, self.runner = policy, worktree, log_dir, runner

    def _log(self, label: str, result: ProcessResult) -> str:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{label}.log"
        path.write_text(f"$ {' '.join(result.args)}\nexit={result.exit_code} seconds={result.seconds}"
                        f"{' TIMED OUT' if result.timed_out else ''}\n\n{result.output}")
        return str(path)

    def ensure_setup(self) -> CommandReport | None:
        """Run setup once per worktree; returns the failed report, or None when setup is done."""
        marker = self.log_dir / "setup.done"
        if not self.policy.setup or marker.exists():
            return None
        result = self.runner(self.policy.validation_image, self.worktree, list(self.policy.setup), SETUP_TIMEOUT,
                             network=True, writable=True)
        log = self._log("setup", result)
        if not result.ok:
            return CommandReport("setup", False, result.exit_code, result.seconds, result.timed_out, log, result.tail())
        marker.write_text("ok\n")
        return None

    def run(self, names: list[str], label: str) -> list[CommandReport]:
        if not self.policy.validation_image:
            raise ValueError("the project's validation_image is not provided by this platform")
        failed_setup = self.ensure_setup()
        if failed_setup:
            return [failed_setup]
        commands = [c for c in self.policy.validation if c.name in names] or list(self.policy.validation)
        reports = []
        for command in commands:
            result = self.runner(self.policy.validation_image, self.worktree, list(command.run), command.timeout_seconds)
            reports.append(CommandReport(command.name, result.ok, result.exit_code, result.seconds, result.timed_out,
                                         self._log(f"{label}-{command.name}", result), result.tail()))
        return reports


def summary(reports: list[CommandReport]) -> str:
    return "; ".join(f"{r.name} {'passed' if r.ok else 'TIMED OUT' if r.timed_out else f'failed (exit {r.exit_code})'}"
                     for r in reports)


def failures(reports: list[CommandReport]) -> str:
    """The failing commands' output, for the Developer's next attempt."""
    return "\n\n".join(f"## {r.name} (exit {r.exit_code})\n{r.tail}" for r in reports if not r.ok)


def red_check(gate: ValidationGate, names: list[str], expect_pass: bool, label: str) -> tuple[bool, str, list[dict]]:
    """New tests must fail before the implementation, unless every case of the task is a regression guard."""
    if not gate.policy.red_check:
        return True, "red check disabled in .sdlc/project.yaml", []
    reports = gate.run(names, label)
    data = [asdict(r) for r in reports]
    if any(r.timed_out for r in reports) or (reports and reports[0].name == "setup"):
        return False, f"red check could not run: {summary(reports)}", data
    if expect_pass:
        ok = all(r.ok for r in reports)
        return ok, ("regression guards pass as expected" if ok else
                    f"every case of this task is a regression guard (expected_pass), but the tests fail on the "
                    f"current code: {summary(reports)}"), data
    ok = any(not r.ok for r in reports)
    return ok, (f"red: {summary(reports)}" if ok else
                f"the new tests already pass before the implementation, so they prove nothing: {summary(reports)}"), data
