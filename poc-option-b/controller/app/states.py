"""Run and task states, allowed transitions, and the human roles that act on them."""

from enum import StrEnum


class RunStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PREFLIGHT = "PREFLIGHT"
    SPECIFYING = "SPECIFYING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    WAITING_SPEC_APPROVAL = "WAITING_SPEC_APPROVAL"
    PLANNING = "PLANNING"
    GROUNDING = "GROUNDING"
    WAITING_PLAN_APPROVAL = "WAITING_PLAN_APPROVAL"
    READY = "READY"
    CODING = "CODING"
    REVIEWING = "REVIEWING"
    PR_OPEN = "PR_OPEN"
    WAITING_PR_APPROVAL = "WAITING_PR_APPROVAL"
    COMPLETED = "COMPLETED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CLOSED_UNMERGED = "CLOSED_UNMERGED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    WRITING_TESTS = "WRITING_TESTS"
    RED_CHECK = "RED_CHECK"
    CODING = "CODING"
    VALIDATING = "VALIDATING"
    REMEDIATING = "REMEDIATING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class RunKind(StrEnum):
    FEATURE = "feature"  # one user story: spec, plan, tests, code, review, PR
    SRS = "srs"  # the project's SRS.md: Business Analyst, SRS gate, PR


class HumanRole(StrEnum):
    PRODUCT_OWNER = "product_owner"
    TECH_LEAD = "tech_lead"
    CODE_OWNER = "code_owner"
    PLATFORM_ADMIN = "platform_admin"


class TeamRole(StrEnum):
    COORDINATOR = "delivery_coordinator"
    BUSINESS_ANALYST = "business_analyst"
    TECH_LEAD = "tech_lead_ai"
    QA_ANALYST = "qa_analyst"
    QA_ENGINEER = "qa_engineer"
    DEVELOPER = "developer"
    VALIDATION_GATE = "validation_gate"
    CODE_REVIEWER = "code_reviewer"
    RELEASE_ENGINEER = "release_engineer"


R = RunStatus
TERMINAL = frozenset({R.COMPLETED, R.FAILED, R.CANCELLED, R.CLOSED_UNMERGED})
ACTIVE = frozenset(R) - TERMINAL

_RUN_FLOW: dict[RunStatus, set[RunStatus]] = {
    R.RECEIVED: {R.PREFLIGHT},
    R.PREFLIGHT: {R.SPECIFYING},
    R.SPECIFYING: {R.NEEDS_CLARIFICATION, R.WAITING_SPEC_APPROVAL, R.PLANNING},
    R.NEEDS_CLARIFICATION: {R.SPECIFYING},
    R.WAITING_SPEC_APPROVAL: {R.PLANNING, R.SPECIFYING, R.PR_OPEN},  # PR_OPEN: an approved SRS goes straight to a PR
    R.PLANNING: {R.GROUNDING},
    R.GROUNDING: {R.PLANNING, R.WAITING_PLAN_APPROVAL},
    R.WAITING_PLAN_APPROVAL: {R.READY, R.PLANNING},
    R.READY: {R.CODING},
    R.CODING: {R.REVIEWING, R.WAITING_PLAN_APPROVAL},
    R.REVIEWING: {R.PR_OPEN},
    R.PR_OPEN: {R.WAITING_PR_APPROVAL},
    R.WAITING_PR_APPROVAL: {R.COMPLETED, R.CODING, R.CLOSED_UNMERGED, R.REVIEWING},  # REVIEWING: review again
    # NEEDS_HUMAN exits only through a recorded human decision.
    R.NEEDS_HUMAN: {R.PREFLIGHT, R.SPECIFYING, R.PLANNING, R.CODING, R.REVIEWING, R.PR_OPEN},
}

def _run_targets(state: RunStatus) -> frozenset[RunStatus]:
    if state in TERMINAL:
        return frozenset()
    escape = {R.FAILED, R.CANCELLED} | ({R.NEEDS_HUMAN} if state != R.NEEDS_HUMAN else set())
    return frozenset(_RUN_FLOW[state] | escape)


RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {state: _run_targets(state) for state in RunStatus}

T = TaskStatus
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    T.PENDING: frozenset({T.READY, T.CANCELLED}),
    T.READY: frozenset({T.WRITING_TESTS, T.CANCELLED}),
    T.WRITING_TESTS: frozenset({T.RED_CHECK, T.UNKNOWN, T.FAILED, T.CANCELLED}),
    T.RED_CHECK: frozenset({T.CODING, T.WRITING_TESTS, T.FAILED, T.CANCELLED}),
    T.CODING: frozenset({T.VALIDATING, T.DISPUTED, T.UNKNOWN, T.FAILED, T.CANCELLED}),
    T.VALIDATING: frozenset({T.PASSED, T.REMEDIATING, T.FAILED, T.CANCELLED}),
    T.REMEDIATING: frozenset({T.VALIDATING, T.DISPUTED, T.UNKNOWN, T.FAILED, T.CANCELLED}),
    T.PASSED: frozenset({T.READY}),  # the Code Owner requested changes
    T.FAILED: frozenset({T.READY}),  # a human resumed the run
    T.DISPUTED: frozenset({T.READY}),
    T.UNKNOWN: frozenset({T.READY}),
    T.CANCELLED: frozenset(),
}

# Which human role an operator acts in when a run waits in a state.
WAITING_FOR: dict[RunStatus, HumanRole] = {
    R.NEEDS_CLARIFICATION: HumanRole.PRODUCT_OWNER,
    R.WAITING_SPEC_APPROVAL: HumanRole.PRODUCT_OWNER,
    R.WAITING_PLAN_APPROVAL: HumanRole.TECH_LEAD,
    R.WAITING_PR_APPROVAL: HumanRole.CODE_OWNER,
    R.NEEDS_HUMAN: HumanRole.TECH_LEAD,
}

# Stages whose autonomy mode is configurable, the state that performs them,
# and the human role that starts them in manual mode.
STAGE_STATES: dict[str, RunStatus] = {
    "spec": R.SPECIFYING,
    "plan": R.PLANNING,
    "coding": R.CODING,
    "review": R.REVIEWING,
}
STAGE_STARTER: dict[str, HumanRole] = {
    "spec": HumanRole.PRODUCT_OWNER,
    "plan": HumanRole.TECH_LEAD,
    "coding": HumanRole.TECH_LEAD,
    "review": HumanRole.CODE_OWNER,
}
