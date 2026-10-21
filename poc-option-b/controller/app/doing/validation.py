"""Validation Gate: allowlisted commands in the project's pinned toolchain container.

Validation runs without network, with the worktree mounted read-only. Setup
commands (for example installing dependencies) run with network and a writable
mount before the first validation, and again when the files the project lists
as setup inputs change. A project with its own environment gets its image built
from its Dockerfile, and its services (a database, say) started in a network of
their own without Internet access for the validation commands. No LLM is involved.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import functools
import hashlib
from pathlib import Path
import time
import uuid

from controller.app.doing.process import CONTAINER_PREFIX, ProcessResult, run_bounded
from controller.app.projects.config import EffectivePolicy

CONTAINER_USER = "1000:1000"


@dataclass(frozen=True)
class Resources:
    """The container's limits, from the project's limits.container."""
    cpus: float = 2
    memory_mb: int = 2048
    pids: int = 512
    tmp_mb: int = 256


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
                   writable: bool = False, resources: Resources = Resources(), network_name: str = "",
                   env: dict[str, str] | None = None) -> list[str]:
    environment = [x for key, value in (env or {}).items() for x in ("-e", f"{key}={value}")]
    return ["docker", "run", "--rm", "--name", name, "--network", network_name or ("bridge" if network else "none"),
            *environment,
            "--cpus", f"{resources.cpus:g}", "--memory", f"{resources.memory_mb}m", "--pids-limit", str(resources.pids),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", CONTAINER_USER,
            "--tmpfs", f"/tmp:rw,size={resources.tmp_mb}m", "-e", "HOME=/tmp", "-e", "CI=1",
            "-v", f"{worktree}:/work:{'rw' if writable else 'ro'}", "-w", "/work", image, *argv]


def run_in_container(image: str, worktree: Path, argv: list[str], timeout: float, *, network: bool = False,
                     writable: bool = False, resources: Resources = Resources(), network_name: str = "",
                     env: dict[str, str] | None = None) -> ProcessResult:
    name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    result = run_bounded(container_args(image, worktree, argv, name=name, network=network, writable=writable,
                                        resources=resources, network_name=network_name, env=env),
                         timeout=timeout, container=name)
    if result.timed_out:  # killing the docker client does not stop the container
        run_bounded(["docker", "rm", "-f", name], timeout=30)
    return result


class ValidationGate:
    """Runs the project's validation commands for one run worktree and keeps their logs as files."""

    def __init__(self, policy: EffectivePolicy, worktree: Path, log_dir: Path, runner=None):
        resources = Resources(policy.container_cpus, policy.container_memory_mb, policy.container_pids,
                              policy.container_tmp_mb)
        self.policy, self.worktree, self.log_dir = policy, worktree, log_dir
        self.runner = runner or functools.partial(run_in_container, resources=resources)

    def _log(self, label: str, result: ProcessResult) -> str:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{label}.log"
        path.write_text(f"$ {' '.join(result.args)}\nexit={result.exit_code} seconds={result.seconds}"
                        f"{' TIMED OUT' if result.timed_out else ''}\n\n{result.output}")
        return str(path)

    def _report(self, name: str, result: ProcessResult) -> CommandReport:
        return CommandReport(name, result.ok, result.exit_code, result.seconds, result.timed_out,
                             self._log(name, result), result.tail())

    def image(self) -> tuple[str, CommandReport | None]:
        """The validation image: the platform's toolchain, or the project's Dockerfile built once per content."""
        if not self.policy.env_dockerfile:
            return self.policy.validation_image, None
        dockerfile = self.worktree / self.policy.env_dockerfile
        if not dockerfile.is_file():
            missing = ProcessResult(["docker", "build"], 1, f"{self.policy.env_dockerfile} is not in the worktree", 0.0, False)
            return "", self._report("environment-build", missing)
        tag = f"{CONTAINER_PREFIX}env-{self.policy.project or 'project'}:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
        if run_bounded(["docker", "image", "inspect", tag], timeout=60).ok:
            return tag, None
        # plain progress, so the log holds the failing command's own output (--quiet hid "no space left on device")
        result = run_bounded(["docker", "build", "--progress=plain", "-f", str(dockerfile), "-t", tag, str(dockerfile.parent)],
                             timeout=self.policy.setup_timeout_seconds)
        return (tag, None) if result.ok else ("", self._report("environment-build", result))

    def _setup_key(self) -> str:
        """What setup depends on: its commands and the content of the setup inputs."""
        digest = hashlib.sha256("\n".join(self.policy.setup).encode())
        for pattern in self.policy.setup_inputs:
            for path in sorted(self.worktree.glob(pattern)):
                if path.is_file():
                    digest.update(str(path.relative_to(self.worktree)).encode() + path.read_bytes())
        return digest.hexdigest()

    def ensure_setup(self, image: str | None = None) -> CommandReport | None:
        """Run setup when it has not run for these inputs yet; returns the failed report, or None when setup is done."""
        marker = self.log_dir / "setup.done"
        key = self._setup_key()
        done = marker.read_text().strip() if marker.exists() else ""
        if not self.policy.setup or done == key or (done == "ok" and not self.policy.setup_inputs):  # "ok": older runs
            return None
        result = self.runner(image or self.policy.validation_image, self.worktree, list(self.policy.setup),
                             self.policy.setup_timeout_seconds,
                             network=True, writable=True)
        log = self._log("setup", result)
        if not result.ok:
            return CommandReport("setup", False, result.exit_code, result.seconds, result.timed_out, log, result.tail())
        marker.write_text(f"{key}\n")
        return None

    @contextmanager
    def services(self):
        """The project's services in a network of their own, without Internet access; yields the network name, or
        a failed report when a service does not get ready."""
        if not self.policy.services:
            yield "", None
            return
        network = f"{CONTAINER_PREFIX}net-{uuid.uuid4().hex[:12]}"
        started: list[str] = []
        try:
            run_bounded(["docker", "network", "create", "--internal", network], timeout=60)
            for service in self.policy.services:
                name = f"{CONTAINER_PREFIX}svc-{uuid.uuid4().hex[:12]}"
                env = [x for key, value in service.env.items() for x in ("-e", f"{key}={value}")]
                result = run_bounded(["docker", "run", "-d", "--rm", "--name", name, "--network", network,
                                      "--network-alias", service.name, "--memory", f"{self.policy.container_memory_mb}m",
                                      "--security-opt", "no-new-privileges", *env, service.image], timeout=300)
                if not result.ok:
                    yield network, self._report(f"service-{service.name}", result)
                    return
                started.append(name)
                if service.ready and not self._wait_ready(name, service):
                    logs = run_bounded(["docker", "logs", "--tail", "50", name], timeout=30)
                    failed = ProcessResult(["docker", "exec", name, *service.ready], 1,
                                           f"{service.name} was not ready after {service.ready_timeout_seconds}s\n"
                                           f"{logs.output}", float(service.ready_timeout_seconds), True)
                    yield network, self._report(f"service-{service.name}", failed)
                    return
            yield network, None
        finally:
            for name in started:
                run_bounded(["docker", "rm", "-f", name], timeout=60)
            run_bounded(["docker", "network", "rm", network], timeout=60)

    @staticmethod
    def _wait_ready(name: str, service) -> bool:
        deadline = time.monotonic() + service.ready_timeout_seconds
        while time.monotonic() < deadline:
            if run_bounded(["docker", "exec", name, *service.ready], timeout=30).ok:
                return True
            time.sleep(1)
        return False

    def run(self, names: list[str], label: str) -> list[CommandReport]:
        if not self.policy.validation_image and not self.policy.env_dockerfile:
            raise ValueError("the project's validation_image is not provided by this platform")
        image, failed = self.image()
        if failed:
            return [failed]
        failed_setup = self.ensure_setup(image)
        if failed_setup:
            return [failed_setup]
        commands = [c for c in self.policy.validation if c.name in names] or list(self.policy.validation)
        reports = []
        with self.services() as (network, failed_service):
            if failed_service:
                return [failed_service]
            extra = {"network_name": network, "env": dict(self.policy.env)} if network or self.policy.env else {}
            for command in commands:
                result = self.runner(image, self.worktree, list(command.run), command.timeout_seconds, **extra)
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
