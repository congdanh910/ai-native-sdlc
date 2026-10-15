from datetime import date

from pydantic import ValidationError
import pytest

from controller.app.project_docs import Srs, check_srs_markdown, render_srs, srs_stories
from controller.app.team import normalize


def answer(**changes) -> dict:
    data = {
        "product": "Attendance", "purpose": "Track attendance.", "scope": "Check-in and reports.",
        "roles": [{"role": "hr", "description": "Reads reports"}, {"role": "Employee", "description": "Checks in"}],
        "entities": [{"name": "Shift", "description": "A planned working period"}],
        "business_rules": [{"statement": "A Shift MUST have an end after its start."},
                           {"statement": "An Employee MAY check in once per Shift."}],
        "features": [
            {"name": "Check-in", "prefix": "ci", "requirements": [{"function": "Check in", "requirement": "The system shall record a check-in."}],
             "stories": [{"title": "Check in", "actors": ["`EMPLOYEE`"], "goal": "check in", "reason": "my hours count"},
                         {"title": "Correct a check-in", "actors": ["HR"], "goal": "fix a check-in", "reason": "pay is right"}]},
            {"name": "Reports", "prefix": "RP",
             "stories": [{"title": "View the monthly report", "actors": ["All internal"], "goal": "see hours",
                          "reason": "I can plan"}]}],
        "flows": [{"name": "Daily check-in", "steps": ["EMPLOYEE faces the camera.", "The system records it."]}],
    }
    data.update(changes)
    return data


def test_render_numbers_ids_and_keeps_the_company_outline():
    markdown = render_srs(Srs.model_validate(answer()), author="Business Analyst (AI)", today=date(2026, 10, 15))
    assert "| BR-01 | A Shift MUST have an end after its start. |" in markdown
    assert "| BR-02 |" in markdown
    assert "| CI-FR-01 | Check in |" in markdown
    assert "#### 7.1.1 CI-US-01: Check in (EMPLOYEE) <a id=\"7-1-1-ci-us-01-check-in-employee\"></a>" in markdown
    assert "**As a** `EMPLOYEE`, **I want to** check in **so that** my hours count." in markdown
    assert "#### 7.2.1 RP-US-01: View the monthly report (All internal)" in markdown
    assert "**As a** All internal, **I want to** see hours" in markdown
    assert "| 0.1 | 2026-10-15 | Business Analyst (AI) | Initial draft |" in markdown
    assert "- [7.1.2 CI-US-02: Correct a check-in (HR)](#7-1-2-ci-us-02-correct-a-check-in-hr)" in markdown
    assert check_srs_markdown(markdown) == []
    assert srs_stories(markdown) == ["CI-US-01", "CI-US-02", "RP-US-01"]


def test_story_actor_must_be_a_declared_role():
    with pytest.raises(ValidationError, match="not in roles"):
        Srs.model_validate(answer(features=[{"name": "Reports", "prefix": "RP", "stories": [
            {"title": "Export", "actors": ["MANAGER"], "goal": "export", "reason": "share"}]}]))


def test_feature_prefixes_are_unique_and_short():
    with pytest.raises(ValidationError, match="unique"):
        Srs.model_validate(answer(features=[answer()["features"][1], answer()["features"][1]]))
    with pytest.raises(ValidationError, match="2-4 upper-case"):
        Srs.model_validate(answer(features=[{**answer()["features"][1], "prefix": "REPORTS"}]))


def test_normalize_turns_strings_into_lists():
    data = normalize.srs(answer(assumptions="- one\n- two", questions=["Which camera?"],
                                features=[{**answer()["features"][1], "stories": [
                                    {"title": "View the report", "actors": "HR, EMPLOYEE", "goal": "see", "reason": "plan"}]}]))
    srs = Srs.model_validate(data)
    assert srs.assumptions == ["one", "two"]
    assert srs.questions[0].blocking is False
    assert srs.features[0].stories[0].actors == ["HR", "EMPLOYEE"]


def test_edited_srs_problems_are_named():
    problems = check_srs_markdown("# SRS\n\n## 1. Introduction\n\n#### 7.1.1 AT-US-01: A (HR)\n#### 7.1.2 AT-US-01: B (HR)\n")
    assert "section '7. Features and User Stories' is missing" in problems
    assert "user story ids appear twice: AT-US-01" in problems


def test_normalize_joins_lists_given_for_text_fields():
    data = normalize.srs(answer(entities=[{"name": "Shift", "description": "A period", "attributes": ["start", "end"]}]))
    assert Srs.model_validate(data).entities[0].attributes == "start, end"
