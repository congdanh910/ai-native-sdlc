from controller.app.config import PROJECT_ROOT
from controller.app.projects.config import effective_policy, parse_project_config
from controller.app.schemas import PlanPayload, Spec, TestPlan
from controller.app.team.tidy import tidy_plan, tidy_spec, tidy_tests


def policy():
    text = (PROJECT_ROOT / "examples/task-board/.sdlc/project.yaml").read_text()
    return effective_policy(parse_project_config(text))  # allowed src/**, tests test/**


def task(id, modify, depends_on=(), criteria=("AC-1",), title="Do something"):
    return {"id": id, "title": title, "modify": list(modify), "depends_on": list(depends_on),
            "acceptance_criteria": list(criteria), "validation": ["unit-tests"]}


def case(id, task_id, criterion):
    return {"id": id, "task_id": task_id, "criterion": criterion, "description": "d", "expected": "e",
            "target_file": "test/tasks.test.js"}


def test_clean_plan_is_unchanged():
    plan = PlanPayload(summary="s", tasks=[task("T1", ["src/tasks.js"])])
    assert tidy_plan(plan, policy()) == (plan, [])


def test_test_files_are_removed_and_test_only_tasks_dropped_with_renumbering():
    plan = PlanPayload(summary="s", tasks=[
        task("T1", ["src/tasks.js", "test/tasks.test.js"]),
        task("T2", ["test/tasks.test.js"], depends_on=["T1"], title="Add tests"),
        task("T3", ["src/app.js"], depends_on=["T2"], criteria=["AC-2"]),
    ])
    tidied, notes = tidy_plan(plan, policy())
    assert [(t.id, t.modify, t.depends_on) for t in tidied.tasks] == [
        ("T1", ["src/tasks.js"], []), ("T2", ["src/app.js"], ["T1"])]
    text = "\n".join(notes)
    assert "T1: removed test file(s) test/tasks.test.js" in text
    assert 'T2: dropped "Add tests"' in text and "T3→T2" in text


def test_plan_of_only_test_tasks_is_kept_for_grounding():
    plan = PlanPayload(summary="s", tasks=[task("T1", ["test/tasks.test.js"])])
    tidied, notes = tidy_plan(plan, policy())
    assert tidied == plan and "kept the plan" in notes[-1]


def test_cases_on_unknown_tasks_move_to_the_task_that_delivers_the_criterion():
    plan = PlanPayload(summary="s", tasks=[task("T1", ["src/tasks.js"]), task("T2", ["src/app.js"], criteria=["AC-2"])])
    tests = TestPlan(cases=[case("TC-1", "T1", "AC-1"), case("TC-2", "T7", "AC-2"), case("TC-3", "T9", "AC-9")])
    tidied, notes = tidy_tests(tests, plan)
    assert [c.task_id for c in tidied.cases] == ["T1", "T2", "T9"]
    assert notes == ["TC-2: moved from unknown task T7 to T2, which delivers AC-2"]


def spec(*texts):
    return Spec(goal="g", user_stories=["s"],
                acceptance_criteria=[{"id": f"AC-{i}", "text": t} for i, t in enumerate(texts, start=1)])


def test_process_criteria_move_to_concerns_and_the_rest_renumber():
    tidied, notes = tidy_spec(spec(
        "The card shows the due date.",
        "All tests in `tests/tasks.test.js` pass when run with `npm test`, including an overdue case.",
        "The code passes syntax checks using `node --check src/app.js`.",
        "A wrong password is rejected.",
        "An invalid date is rejected and saved data is kept."), policy())
    assert [(c.id, c.text[:12]) for c in tidied.acceptance_criteria] == [
        ("AC-1", "The card sho"), ("AC-2", "A wrong pass"), ("AC-3", "An invalid d")]
    assert len(tidied.concerns) == 2 and tidied.concerns[0].startswith("Covered by validation, not a criterion: All tests")
    assert notes[:2] == ["AC-2: moved to concerns; validation runs tests and checks, so it is not a criterion",
                         "AC-3: moved to concerns; validation runs tests and checks, so it is not a criterion"]
    assert "AC-4→AC-2" in notes[2] and "AC-5→AC-3" in notes[2]


def test_behavioural_spec_is_unchanged():
    clean = spec("The card shows the due date.", "Tasks without a due date still load.")
    assert tidy_spec(clean, policy()) == (clean, [])
