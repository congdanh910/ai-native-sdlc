from controller.app.context.builder import build_context, keywords, symbols_of
from controller.app.context.grounding import check_plan
from controller.app.projects.config import effective_policy, parse_project_config
from controller.app.projects.repo import Mirror
from controller.app.schemas import Criterion, PlanPayload, TestPlan
from controller.tests.conftest import git
from controller.app.config import PROJECT_ROOT

JS = """export const STORAGE_KEY = "board:v1";
export function createTask(tasks, input) { return tasks; }
function render() {}
class Board {}
"""


def test_symbols_for_js_and_python():
    assert symbols_of("src/tasks.js", JS) == ["createTask", "STORAGE_KEY", "render", "Board"]
    assert symbols_of("app/x.py", "def load():\n    pass\nclass Store:\n    pass\nLIMIT = 3\n") == ["load", "Store", "LIMIT"]
    assert symbols_of("README.md", "# x") == []


def test_keywords_drop_short_and_common_words():
    assert keywords("Each task can have an optional due date") == {"optional", "date"}


def test_bundle_prefers_relevant_files_and_their_imports(sample_repo, tmp_path):
    (sample_repo / "src" / "dates.js").write_text("export function parseDueDate(text) { return text; }\n")
    (sample_repo / "src" / "app.js").write_text("import { parseDueDate } from './dates.js';\n")
    (sample_repo / "src" / "other.js").write_text("export function unrelated() {}\n")
    git(sample_repo, "add", ".")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "more")
    mirror = Mirror(tmp_path / "m.git")
    mirror.clone(str(sample_repo))
    commit = mirror.resolve("main")
    files = mirror.list_files(commit)
    bundle = build_context(mirror, commit, files, ["Show due date on src/app.js cards"], budget=5000)
    selected = [f["path"] for f in bundle.selected_files]
    assert selected[0] == "src/app.js" and "src/dates.js" in selected
    assert "parseDueDate" in bundle.symbols["src/dates.js"]
    small = build_context(mirror, commit, files, ["due date"], budget=0)
    assert small.selected_files == [] and small.symbols


def policy():
    text = (PROJECT_ROOT / "examples/task-board/.sdlc/project.yaml").read_text()
    return effective_policy(parse_project_config(text))


FILES = {"src/tasks.js", "src/app.js", "test/tasks.test.js"}
SYMBOLS = {"src/tasks.js": {"createTask", "STORAGE_KEY"}}
CRITERIA = [Criterion(id="AC-1", text="a"), Criterion(id="AC-2", text="b")]


def plan(**task):
    base = {"id": "T1", "title": "Add due date", "modify": ["src/tasks.js"], "acceptance_criteria": ["AC-1", "AC-2"],
            "validation": ["unit-tests"], "references": [{"file": "src/tasks.js", "symbol": "createTask"}]}
    return PlanPayload(summary="s", tasks=[{**base, **task}])


def tests(*cases):
    default = [{"id": "TC-1", "task_id": "T1", "criterion": "AC-1", "description": "d", "expected": "e",
                "target_file": "test/tasks.test.js"},
               {"id": "TC-2", "task_id": "T1", "criterion": "AC-2", "description": "d", "expected": "e",
                "target_file": "test/tasks.test.js"}]
    return TestPlan(cases=list(cases) or default)


def test_grounded_plan_passes():
    assert check_plan(plan(), tests(), CRITERIA, policy(), FILES, SYMBOLS) == {"status": "pass", "findings": [],
                                                                              "warnings": []}


def test_markup_without_stylesheet_is_a_warning_not_a_failure():
    files = FILES | {"src/index.html", "src/styles.css"}
    report = check_plan(plan(modify=["src/tasks.js", "src/index.html"]), tests(), CRITERIA, policy(), files, SYMBOLS)
    assert report["status"] == "pass" and "no stylesheet (src/styles.css)" in report["warnings"][0]
    styled = check_plan(plan(modify=["src/index.html", "src/styles.css"]), tests(), CRITERIA, policy(), files, SYMBOLS)
    assert styled["warnings"] == []


def test_grounding_catches_the_pilot_mistakes():
    bad = plan(modify=["src/board.js"], references=[{"file": "src/app.js", "symbol": "renderBoard"}],
               create=["src/tasks.js"], validation=["lint"])
    findings = check_plan(bad, tests(), CRITERIA, policy(), FILES, SYMBOLS)["findings"]
    text = "\n".join(findings)
    assert "src/board.js, which does not exist" in text
    assert "renderBoard in src/app.js, which is not defined there" in text
    assert "creates src/tasks.js, which already exists" in text
    assert "unknown validation lint" in text


def test_grounding_checks_paths_and_coverage():
    bad = plan(modify=["test/tasks.test.js", ".github/workflows/ci.yml"], acceptance_criteria=["AC-1", "AC-9"])
    one_case = tests({"id": "TC-1", "task_id": "T7", "criterion": "AC-1", "description": "d", "expected": "e",
                      "target_file": "src/tasks.js"})
    text = "\n".join(check_plan(bad, one_case, CRITERIA, policy(), FILES | {".github/workflows/ci.yml"}, SYMBOLS)["findings"])
    for expected in ("changes test file test/tasks.test.js", "touches protected path .github/workflows/ci.yml",
                     "unknown acceptance criterion AC-9", "no task delivers AC-2", "unknown task T7",
                     "outside the test paths", "no test case proves AC-2"):
        assert expected in text, expected


def test_references_to_symbols_added_by_a_dependency_are_grounded():
    tasks = [
        {"id": "T1", "title": "Add filter model", "modify": ["src/tasks.js"], "provides": ["filterTasks"],
         "acceptance_criteria": ["AC-1"], "validation": ["unit-tests"]},
        {"id": "T2", "title": "Wire the filter", "depends_on": ["T1"], "modify": ["src/app.js"],
         "references": [{"file": "src/tasks.js", "symbol": "filterTasks"}], "acceptance_criteria": ["AC-2"],
         "validation": ["unit-tests"]},
        {"id": "T3", "title": "Unrelated", "modify": ["src/app.js"],
         "references": [{"file": "src/tasks.js", "symbol": "filterTasks"}], "acceptance_criteria": ["AC-2"],
         "validation": ["unit-tests"]},
    ]
    report = check_plan(PlanPayload(summary="s", tasks=tasks), tests(), CRITERIA, policy(), FILES, SYMBOLS)
    assert report["findings"] == ["T3 references filterTasks in src/tasks.js, which is not defined there"]
