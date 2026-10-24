"""Next steps for the person at a gate when a controller check blocks it: why it blocks, which decision to take, and
a reason written for the role that redoes the work. People seldom know what to do with "High: TC-23 proves AC-12 on
T2, which neither delivers it nor depends on a task that does" (Manage Departments, 23/10).

Actions name the decision, not a button of one gate: replan (the Tech Lead AI plans again; the test cases follow),
redesign_tests (the QA Analyst designs the test cases again), rewrite_spec (the Business Analyst writes the spec
again), revise_onboarding (the Architect revises the onboarding files). The page maps them to the gate's buttons.
"""


def fix(action: str, why: str, reason: str) -> dict:
    return {"action": action, "why": why, "reason": reason}


# what to do when a controller check of a gate fails, for checks whose findings carry no fix of their own
CHECK_FIXES = {
    "spec": {
        "parse": ("rewrite_spec", "The spec does not follow the company template, so its criteria cannot be read.",
                  "Write the criteria in the template format: AC-01 … and EC-01 … with Given, When and Then."),
        "one-story": ("rewrite_spec", "A story's spec section holds one user story statement.",
                      "Keep only this story's statement (**As a** …) in the section."),
        "ids": ("rewrite_spec", "Criteria ids must run AC-01, AC-02 … and EC-01 … without gaps.", "Renumber the criteria without gaps: {detail}."),
        "gwt": ("rewrite_spec", "Each criterion needs Given, When and Then to be testable.", "Complete Given, When and Then: {detail}."),
        "banned-words": ("rewrite_spec", "Vague words cannot be tested.", "Replace the vague words with measurable terms: {detail}."),
        "names": ("rewrite_spec", "Short names identify criteria in tests and reports.", "Give each criterion a unique short name: {detail}."),
        "no-paths": ("rewrite_spec", "Criteria describe behaviour for the business, not code.", "Describe behaviour without API paths or file names: {detail}."),
    },
    "plan": {
        "delivered": ("replan", "Every criterion needs a task that builds it.", "Assign these criteria to the tasks that build them: {detail}."),
        "files": ("replan", "The controller only lets a task change the files it names.", "Name the files each task changes: {detail}."),
        "no-tests": ("replan", "Tests belong to the QA Engineer; the Developer's tasks change code only.",
                     "Take the test files out of the tasks' files: {detail}."),
        "placeholders": ("replan", "A plan with TBD cannot be built.", "Replace every placeholder with the real decision."),
        "grounding": ("replan", "The plan names files, functions or imports that do not exist and no earlier task creates.",
                      "Fix these references against the repository, or let an earlier task create them: {detail}."),
    },
    "tests": {
        "coverage": ("redesign_tests", "Every criterion needs a test case.", "Add a test case for each criterion that has none ({detail})."),
        "known-criteria": ("redesign_tests", "Test cases may prove only this story's criteria.", "Use only this spec's AC and EC ids: {detail}."),
        "known-tasks": ("redesign_tests", "Test cases run with a task of the approved plan.", "Put each test case on a task of the plan: {detail}."),
        "test-paths": ("redesign_tests", "Tests outside the project's test paths are never run by validation.",
                       "Put the test files under the project's test paths: {detail}."),
        "test-run": ("redesign_tests", "No validation command runs these test files, so the red check and the "
                                       "validation would pass without them.",
                     "Put each test case in a folder the validation runs: {detail}."),
        "fields": ("redesign_tests", "A test case without a scenario or an expected result cannot be written.",
                   "Give every test case a scenario and an expected result: {detail}."),
    },
}


def check_fixes(gate: str, item_id: str, detail: str) -> list[dict]:
    known = CHECK_FIXES.get(gate, {}).get(item_id)
    if not known:
        return []
    action, why, reason = known
    return [fix(action, why, reason.format(detail=detail))]
