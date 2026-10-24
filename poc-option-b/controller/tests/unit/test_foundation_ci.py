"""The CI workflow the Architect writes runs with the variables validation runs with."""

from controller.app.foundation import ci_env_gaps

CONFIG = """schema_version: 1
project: attendance
validation:
- name: unit
  run: [pytest]
paths: {allowed: ["backend/**"], tests: ["backend/tests/**"]}
executors: [codex]
default_executor: codex
environment:
  dockerfile: .sdlc/validation.Dockerfile
  env:
    APP_ENV: test
    SESSION_COOKIE_SECURE: 'false'
"""


def test_a_variable_of_the_validation_environment_missing_in_ci_is_found():
    ci = "on: pull_request\njobs:\n  validate:\n    runs-on: ubuntu-latest\n    env:\n      APP_ENV: test\n"
    assert ci_env_gaps({".github/workflows/ci.yml": ci, ".sdlc/project.yaml": CONFIG}) == [
        ".github/workflows/ci.yml does not set SESSION_COOKIE_SECURE (environment.env has SESSION_COOKIE_SECURE=false)"]
    full = ci + "      SESSION_COOKIE_SECURE: 'false'\n"
    assert ci_env_gaps({".github/workflows/ci.yml": full, ".sdlc/project.yaml": CONFIG}) == []
    assert ci_env_gaps({".sdlc/project.yaml": CONFIG}) == []  # no CI workflow: nothing to compare
