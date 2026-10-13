import pytest

from scripts.reconcile_pilot_ci import verify_checks


def test_manual_ci_reconciliation_requires_complete_exact_github_actions_checks():
    sha = "a" * 40
    check = {
        "id": 101, "name": "task-board-tests", "head_sha": sha,
        "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"},
    }
    assert verify_checks({"total_count": 2, "check_runs": [check, {**check, "id": 102}]},
                         name="task-board-tests", sha=sha) == [101, 102]
    for invalid in (
        {"total_count": 0, "check_runs": []},
        {"total_count": 2, "check_runs": [check]},
        {"total_count": 1, "check_runs": [{**check, "head_sha": "b" * 40}]},
        {"total_count": 1, "check_runs": [{**check, "status": "in_progress"}]},
        {"total_count": 1, "check_runs": [{**check, "conclusion": "failure"}]},
        {"total_count": 1, "check_runs": [{**check, "app": {"slug": "other"}}]},
        {"total_count": 2, "check_runs": [check, check]},
    ):
        with pytest.raises(ValueError):
            verify_checks(invalid, name="task-board-tests", sha=sha)
