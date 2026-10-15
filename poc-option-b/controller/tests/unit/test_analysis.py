from controller.app import analysis
from controller.app.schemas import Criterion, PlanPayload, TestPlan

CRITERIA = [Criterion(id="AC-01", text="Due date", given="a task", when="saved", then="has a date"),
            Criterion(id="EC-01", text="Empty date", given="a task", when="no date", then="no badge")]


def plan(*tasks) -> PlanPayload:
    return PlanPayload(summary="s", tasks=[{"id": t[0], "title": "Do it", "modify": ["src/tasks.js"],
                                            "acceptance_criteria": t[1], "validation": ["unit"]} for t in tasks])


def cases_of(*cases) -> TestPlan:
    return TestPlan(cases=[{"id": c[0], "task_id": c[1], "criterion": c[2], "description": "d", "expected": "e",
                            "target_file": "test/tasks.test.js"} for c in cases])


def test_before_coding_finds_gaps_between_spec_plan_and_tests():
    findings = analysis.before_coding(CRITERIA, plan(("T1", ["AC-01", "AC-09"]), ("T2", ["AC-01"])),
                                      cases_of(("TC-1", "T1", "AC-01"), ("TC-2", "T2", "EC-01")))
    texts = {(f["severity"], f["text"]) for f in findings}
    assert ("Critical", "EC-01 is delivered by no task") in texts
    assert ("High", "T1 delivers AC-09, which the spec does not have") in texts
    assert ("High", "TC-2 proves EC-01 on T2, which does not deliver it") in texts
    assert analysis.blocking(findings)


def test_agreeing_artifacts_have_no_findings():
    good = analysis.before_coding(CRITERIA, plan(("T1", ["AC-01", "EC-01"])),
                                  cases_of(("TC-1", "T1", "AC-01"), ("TC-2", "T1", "EC-01")))
    assert good == []
    assert "No findings" in analysis.render("Due dates", "before", good)


def test_after_coding_flags_unplanned_changes_and_untested_files():
    findings = analysis.after_coding(plan(("T1", ["AC-01"])), cases_of(("TC-1", "T1", "AC-01")), {"T1": "PASSED"},
                                     ["src/tasks.js", "src/secret.js", "specs/001-x/plan.md"], ["test/**"])
    texts = {(f["severity"], f["text"]) for f in findings}
    assert texts == {("High", "src/secret.js changed but no task plans it"),
                     ("High", "test/tasks.test.js holds planned test cases but was not changed")}
