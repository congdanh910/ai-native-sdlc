"""Read-only GX10 capacity sample: available RAM and GPU utilization."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from controller.app.config import Settings

MEMORY_COMMAND = "free -m"
GPU_COMMAND = "nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits"


@dataclass(frozen=True)
class CapacitySample:
    available_mib: int
    gpu_util_percent: int


def parse_memory(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("Mem:"):
            fields = line.split()
            if len(fields) >= 7 and fields[6].isdigit():
                return int(fields[6])
    raise ValueError("cannot read available memory from `free -m`")


def parse_gpu_utilization(output: str) -> int:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1 or not re.fullmatch(r"\d+", lines[0]):
        raise ValueError("cannot read GPU utilization from nvidia-smi")
    return int(lines[0])


def evaluate(sample: CapacitySample, *, min_available_mib: int, max_gpu_util: int) -> list[str]:
    reasons = []
    if sample.available_mib < min_available_mib:
        reasons.append(f"available RAM {sample.available_mib} MiB is below {min_available_mib} MiB")
    if sample.gpu_util_percent > max_gpu_util:
        reasons.append(f"GPU utilization {sample.gpu_util_percent}% is above {max_gpu_util}%")
    return reasons


def _remote_read(settings: Settings, command: str) -> str:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-p", str(settings.gx10_ssh_port),
         "-i", str(Path(settings.gx10_ssh_identity_file).expanduser()),
         "--", settings.gx10_ssh_host, command],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"SSH read failed with exit code {result.returncode}")
    return result.stdout


def sample(settings: Settings) -> CapacitySample:
    return CapacitySample(
        available_mib=parse_memory(_remote_read(settings, MEMORY_COMMAND)),
        gpu_util_percent=parse_gpu_utilization(_remote_read(settings, GPU_COMMAND)),
    )
