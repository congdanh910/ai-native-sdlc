"""Choose an isolated PR base for each fixed Task Board benchmark feature."""

import re
from pathlib import Path


def feature_id_for_requirement(manifest: dict, requirement: str, project_root: Path) -> str:
    for item in manifest["tasks"]:
        feature_id = item["id"]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", feature_id):
            raise ValueError("invalid benchmark feature id")
        path = project_root / "fixtures/task-board" / item["requirement_file"]
        if path.read_text() == requirement:
            return feature_id
    raise ValueError("run requirement does not match a fixed benchmark feature")


def pr_base_branch(manifest: dict, feature_id: str) -> str:
    if feature_id not in {item["id"] for item in manifest["tasks"]}:
        raise ValueError("feature is not in the fixed benchmark")
    if feature_id == "search-filter":
        return manifest["baseline_branch"]
    return f"pilot/benchmark-base-{feature_id}"
