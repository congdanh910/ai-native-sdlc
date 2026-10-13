import json
from pathlib import Path

import pytest

from scripts.pilot_branches import feature_id_for_requirement, pr_base_branch


def test_benchmark_features_have_isolated_pr_bases():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "fixtures/task-board/benchmark.json").read_text())
    for item in manifest["tasks"]:
        requirement = (root / "fixtures/task-board" / item["requirement_file"]).read_text()
        feature_id = feature_id_for_requirement(manifest, requirement, root)
        assert feature_id == item["id"]
        expected = (manifest["baseline_branch"] if feature_id == "search-filter"
                    else f"pilot/benchmark-base-{feature_id}")
        assert pr_base_branch(manifest, feature_id) == expected
    with pytest.raises(ValueError, match="requirement"):
        feature_id_for_requirement(manifest, "unlisted requirement", root)
