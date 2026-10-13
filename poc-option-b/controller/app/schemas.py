from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreate(StrictModel):
    repo_id: str = Field(min_length=1, max_length=255)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$")
    requirement: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    executor: Literal["codex", "claude"] = "codex"


class PlanTask(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=255)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)

    @field_validator("allowed_paths")
    @classmethod
    def validate_paths(cls, paths: list[str]) -> list[str]:
        for path in paths:
            parsed = PurePosixPath(path)
            if (
                not path or path == "." or path.startswith("/") or ".." in parsed.parts
                or "\\" in path or "\x00" in path or parsed.as_posix() != path
            ):
                raise ValueError("allowed paths must be relative POSIX paths without traversal")
        return paths


class PlanPayload(StrictModel):
    tasks: list[PlanTask] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_dag(self) -> "PlanPayload":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        dependencies = {task.id: set(task.dependencies) for task in self.tasks}
        for task_id, parents in dependencies.items():
            if task_id in parents or not parents.issubset(dependencies):
                raise ValueError("dependencies must refer to other tasks in the plan")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for parent in dependencies[task_id]:
                visit(parent)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)
        return self


class AnalysisResult(StrictModel):
    summary: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ReviewFinding(StrictModel):
    severity: str = Field(pattern=r"^(low|medium|high)$")
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ReviewResult(StrictModel):
    verdict: str = Field(pattern=r"^(pass|needs_changes)$")
    findings: list[ReviewFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_finding_for_changes(self) -> "ReviewResult":
        if self.verdict == "needs_changes" and not self.findings:
            raise ValueError("needs_changes requires at least one finding")
        return self


class PlanDecision(StrictModel):
    plan_id: UUID
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_run_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class PlanRevision(StrictModel):
    expected_run_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1)
    plan: PlanPayload


class VersionedDecision(StrictModel):
    expected_run_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class FailureClassification(StrictModel):
    category: str = Field(pattern=r"^(AMBIGUOUS_SPEC|MISSING_CONTEXT|WEAK_TESTS|MODEL_LIMITATION|INFRASTRUCTURE|OTHER)$")
    stage: str = Field(pattern=r"^(analysis|planning|coding|validation|review)$")
    note: str = Field(min_length=1)
    corrective_action: str = Field(min_length=1)
    actor: str = Field(min_length=1, max_length=255)
