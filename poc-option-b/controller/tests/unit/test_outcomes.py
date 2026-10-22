"""Desired outcomes suggested from the SRS and SDS of a user story."""

from controller.app.outcomes import story_context, suggest

SRS = """#### 7.2.1 FEN-US-01: Register Face Data (EMPLOYEE)

**As a** `EMPLOYEE`, **I want to** enroll my face data with my consent **so that** I can attend.

| No. | Function | Requirement | Business Rules |
|---|---|---|---|
| FEN-FR-01 | Enroll face data | The system shall register an employee's face data after consent is given. | Consent is required. |
| FEN-FR-02 | Update face data | The system shall allow face data to be updated. | Consent is required for use. |

| REQ | Type | Name | Traces |
|---|---|---|---|
| REQ-007 | feature | Face Enrollment | FEN-FR-01; FEN-US-01 |
| REQ-011 | data | Face data | FEN-FR-01; FEN-FR-02; FEN-US-01 |
"""
SDS = """| `API-FEN-02` | FEN-US-01 | Record consent and register the linked employee's face template. |
| `API-FEN-03` | FEN-US-02 | Replace the template. |

#### API-FEN-02 POST /api/v1/me/face

Errors: `AUTHENTICATION_REQUIRED`, `FACE_CAPTURE_INVALID`

| 422 | `FACE_CAPTURE_INVALID` | The image cannot yield a usable single-face template. | Capture again. |
"""


def test_outcomes_come_from_the_story_s_own_requirements_apis_and_errors():
    context = story_context(SRS, SDS, "FEN-US-01")
    assert "enroll my face data" in context["statement"]
    assert [r["id"] for r in context["requirements"]] == ["FEN-FR-01"]  # traced most often to the story
    assert [a["id"] for a in context["apis"]] == ["API-FEN-02"]
    lines = suggest(context)
    assert [l["source"] for l in lines] == ["API-FEN-02", "FEN-FR-01", "FEN-FR-01 rule", "FACE_CAPTURE_INVALID"]
    assert lines[1]["text"] == "Register an employee's face data after consent is given"
    assert lines[3]["text"].startswith("When the image cannot yield a usable single-face template")  # no generic error


def test_without_traceability_every_requirement_of_the_feature_is_offered():
    srs = SRS.split("| REQ |")[0]
    assert [r["id"] for r in story_context(srs, "", "FEN-US-01")["requirements"]] == ["FEN-FR-01", "FEN-FR-02"]
