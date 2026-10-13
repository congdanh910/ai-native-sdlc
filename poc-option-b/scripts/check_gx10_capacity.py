"""Read-only capacity gate to run immediately before any GX10 model load."""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


REMOTE_COMMANDS = {
    "memory": "free -m",
    "gpu": "nvidia-smi --query-gpu=name,utilization.gpu --format=csv,noheader,nounits",
    "containers": "sudo -n docker ps --format '{{.Names}}\\t{{.Status}}'",
}


def parse_memory(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("Mem:"):
            fields = line.split()
            if len(fields) >= 7:
                return int(fields[6])
    raise ValueError("cannot read available memory")


def parse_gpu_utilization(output: str) -> int:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("expected one GPU")
    fields = [field.strip() for field in lines[0].split(",")]
    if len(fields) != 2 or not re.fullmatch(r"\d+", fields[1]):
        raise ValueError("GPU utilization is unavailable")
    return int(fields[1])


def parse_containers(output: str) -> dict[str, str]:
    containers: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        name, sep, status = line.partition("\t")
        if not sep or not name or not status:
            raise ValueError("cannot parse container status")
        containers[name] = status
    if not containers:
        raise ValueError("no running containers were reported")
    return containers


def evaluate(
    samples: list[dict], *, min_available_mib: int, max_gpu_util: int,
    allowed_unhealthy: frozenset[str] = frozenset(),
) -> list[str]:
    reasons: list[str] = []
    basenames = set(samples[0]["containers"])
    baseline_unhealthy = {
        name for name, status in samples[0]["containers"].items()
        if "(unhealthy)" in status
    }
    for index, sample in enumerate(samples, start=1):
        if sample["available_mib"] < min_available_mib:
            reasons.append(f"sample {index}: available RAM below {min_available_mib} MiB")
        if sample["gpu_util_percent"] > max_gpu_util:
            reasons.append(f"sample {index}: GPU utilization above {max_gpu_util}%")
        if set(sample["containers"]) != basenames:
            reasons.append(f"sample {index}: running container set changed")
        unhealthy = {
            name for name, status in sample["containers"].items()
            if "(unhealthy)" in status
        }
        unexpected = sorted(unhealthy - baseline_unhealthy | unhealthy - allowed_unhealthy)
        if unexpected:
            reasons.append(f"sample {index}: unhealthy applications not in stable allowlist: {', '.join(unexpected)}")
    return reasons


def remote_read(host: str, port: int, identity_file: Path, command: str) -> str:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-p", str(port), "-i", str(identity_file), host, command],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"SSH read failed with exit code {result.returncode}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SSH destination, for example cdp@example-host")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--min-available-mib", type=int, default=65536)
    parser.add_argument("--max-gpu-util", type=int, default=20)
    parser.add_argument("--allow-unhealthy", action="append", default=[], metavar="CONTAINER")
    args = parser.parse_args()
    if args.samples < 2 or args.interval_seconds <= 0 or args.min_available_mib <= 0:
        parser.error("samples must be at least 2; interval and memory threshold must be positive")
    if not 0 <= args.max_gpu_util <= 100:
        parser.error("GPU utilization threshold must be 0..100")
    if not args.identity_file.is_file():
        parser.error("identity file does not exist")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) for name in args.allow_unhealthy):
        parser.error("invalid allowed unhealthy container name")
    samples = []
    try:
        for index in range(args.samples):
            if index:
                time.sleep(args.interval_seconds)
            values = {
                name: remote_read(args.host, args.port, args.identity_file, command)
                for name, command in REMOTE_COMMANDS.items()
            }
            samples.append({
                "available_mib": parse_memory(values["memory"]),
                "gpu_util_percent": parse_gpu_utilization(values["gpu"]),
                "containers": parse_containers(values["containers"]),
            })
        reasons = evaluate(
            samples, min_available_mib=args.min_available_mib,
            max_gpu_util=args.max_gpu_util,
            allowed_unhealthy=frozenset(args.allow_unhealthy),
        )
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"capacity_gate_passed": False, "reasons": [str(exc)]}, indent=2))
        return 2
    print(json.dumps({
        "capacity_gate_passed": not reasons,
        "available_mib": [sample["available_mib"] for sample in samples],
        "gpu_util_percent": [sample["gpu_util_percent"] for sample in samples],
        "running_containers": len(samples[-1]["containers"]),
        "allowed_unhealthy": sorted(args.allow_unhealthy),
        "reasons": reasons,
    }, indent=2))
    return 0 if not reasons else 2


if __name__ == "__main__":
    raise SystemExit(main())
