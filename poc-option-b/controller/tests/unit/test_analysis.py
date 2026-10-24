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
    assert ("High", "TC-2 proves EC-01 on T2, which neither delivers it nor depends on a task that does") in texts
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


def test_a_test_on_a_later_task_may_prove_what_its_dependency_built():
    layered = PlanPayload(summary="s", tasks=[
        {"id": "T1", "title": "Store", "modify": ["src/store.js"], "acceptance_criteria": ["EC-01"], "validation": ["u"]},
        {"id": "T2", "title": "Server", "modify": ["src/server.js"], "depends_on": ["T1"], "acceptance_criteria": ["AC-01"],
         "validation": ["u"]}])
    findings = analysis.before_coding(CRITERIA, layered, cases_of(("TC-1", "T2", "AC-01"), ("TC-2", "T2", "EC-01")))
    assert [f for f in findings if f["severity"] in analysis.BLOCKING] == []


def test_files_a_fix_or_conflict_task_may_change_count_as_planned():
    findings = analysis.after_coding(plan(("T1", ["AC-01"])), cases_of(("TC-1", "T1", "AC-01")), {"T1": "PASSED"},
                                     ["src/tasks.js", "test/tasks.test.js", "src/a.js",
                                      "backend/migrations/versions/0005_merge.py"], [],
                                     also_allowed=["backend/**"])
    assert [f["text"] for f in findings if f["category"] == "scope" and f["severity"] == "High"] == [
        "src/a.js changed but no task plans it"]  # outside what the added task may change


def test_planned_tests_the_task_wrote_in_another_folder_are_delivered():
    changed = ["src/tasks.js", "test/unit/tasks.test.js"]
    moved = analysis.after_coding(plan(("T1", ["AC-01"])), cases_of(("TC-1", "T1", "AC-01")), {"T1": "PASSED"},
                                  changed, ["test/**"], written={"T1": ["test/unit/tasks.test.js"]})
    assert [(f["severity"], f["text"]) for f in moved] == [
        ("Low", "test/tasks.test.js holds planned test cases of T1; they were written in test/unit/tasks.test.js")]
    lost = analysis.after_coding(plan(("T1", ["AC-01"])), cases_of(("TC-1", "T1", "AC-01")), {"T1": "PASSED"},
                                 ["src/tasks.js"], ["test/**"], written={"T1": []})
    assert ("High", "test/tasks.test.js holds planned test cases but was not changed") in \
        [(f["severity"], f["text"]) for f in lost]
