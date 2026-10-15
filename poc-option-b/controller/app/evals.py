"""Plan-only evals: run the thinking roles on fixed features and compare with human-approved plans.

The playbook treats agent configuration (prompts, model, context rules) as code that
needs regression tests. An eval creates one experiment run per case, pinned to the
suite's base commit, with the spec stage on auto. Runs stop at the plan gate; the
eval scores the plans and then cancels the runs, which never reach coding.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
import yaml

from controller.app import services
from controller.app.config import PROJECT_ROOT, get_settings
from controller.app.documents import latest_document, sha256_text
from controller.app.events import record_event
from controller.app.integrations.langflow import ROLES, role_prompt
from controller.app.models import Artifact, Clarification, EvalRun, Execution, Plan, Project, Run
from controller.app.projects.config import EffectivePolicy, matches_any
from controller.app.schemas import Intent, parse_criteria
from controller.app.states import RunStatus
from controller.app.team.readonly import validate_engines

EVALS_DIR = PROJECT_ROOT / "evals"
CASE_DONE = {RunStatus.WAITING_PLAN_APPROVAL, RunStatus.NEEDS_CLARIFICATION, RunStatus.NEEDS_HUMAN,
             RunStatus.FAILED, RunStatus.CANCELLED}
ACTOR = "eval-runner"


@dataclass(frozen=True)
class Case:
    id: str
    intent: Intent
    golden: dict


@dataclass(frozen=True)
class Suite:
    name: str
    project: str
    base_commit: str
    cases: list[Case]


def parse_feature(markdown: str) -> Intent:
    """Feature files: `# Feature n: Title`, a user story paragraph, then `- ` acceptance bullets."""
    title_match = re.search(r"^# (?:Feature \d+:\s*)?(.+)$", markdown, re.M)
    if not title_match:
        raise ValueError("feature file needs a `# Title` line")
    body = markdown[title_match.end():].strip()
    story = body.split("\n\n", 1)[0].strip()
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    return Intent(title=title_match[1].strip(), problem=story, outcome="\n".join(bullets) or f"- {story}")


def list_suites(root: Path | None = None) -> list[str]:
    return sorted(path.parent.name for path in (root or EVALS_DIR).glob("*/suite.yaml"))


def load_suite(name: str, root: Path | None = None) -> Suite:
    if name not in list_suites(root):
        raise LookupError(f"unknown eval suite {name!r}")
    folder = (root or EVALS_DIR) / name
    data = yaml.safe_load((folder / "suite.yaml").read_text())
    cases = [Case(id=item["id"], intent=parse_feature((folder / item["feature"]).read_text()),
                  golden=json.loads((folder / item["golden"]).read_text())) for item in data["cases"]]
    return Suite(name=data["name"], project=data["project"], base_commit=data["base_commit"], cases=cases)


def fingerprint(engines: dict | None = None) -> dict:
    """Everything platform-side that can change what the thinking roles produce."""
    settings = get_settings()
    return {"team_mode": settings.sdlc_team_mode, "model": settings.ollama_model,
            "num_ctx": settings.ollama_num_ctx, "context_char_budget": settings.context_char_budget,
            "prompts": {role: role_prompt(role).version for role in ROLES},
            "engines": validate_engines(engines)}


def start_eval(session: Session, suite_name: str, *, actor: str, trigger: str = "manual",
               engines: dict | None = None) -> EvalRun:
    try:
        engines = validate_engines(engines)
    except ValueError as exc:
        raise services.DecisionError(str(exc)) from exc
    suite = load_suite(suite_name)
    project = session.scalar(select(Project).where(Project.key == suite.project))
    if project is None:
        raise services.DecisionError(f"register project {suite.project} before running this suite")
    if session.scalar(select(EvalRun).where(EvalRun.suite == suite.name, EvalRun.status == "running")):
        raise services.DecisionError(f"an eval of {suite.name} is already running")
    baseline = session.scalar(select(EvalRun).where(EvalRun.suite == suite.name, EvalRun.status == "completed")
                              .order_by(EvalRun.created_at.desc()).limit(1))
    evaluation = EvalRun(suite=suite.name, scope="plan", trigger=trigger, fingerprint=fingerprint(engines),
                         status="running", actor=actor, baseline_id=baseline.id if baseline else None)
    session.add(evaluation)
    session.flush()
    for case in suite.cases:
        run = services.create_run(session, project, case.intent, actor=ACTOR,
                                  idempotency_key=f"eval:{evaluation.id}:{case.id}", experiment=True,
                                  base_commit=suite.base_commit, stage_overrides={"spec": "auto"},
                                  eval_run_id=evaluation.id, eval_case=case.id)
        session.add(Artifact(run_id=run.id, kind="engines", role="coordinator", content=json.dumps(engines),
                             checksum=sha256_text(json.dumps(engines))))
    record_event(session, "eval.started", actor=actor, payload={"eval": str(evaluation.id), "suite": suite.name})
    return evaluation


def _source_files(paths, policy: EffectivePolicy) -> set[str]:
    return {p for p in paths if not matches_any(p, policy.test_paths)}


def _usage(session: Session, run: Run) -> dict:
    """Model calls and subscription usage of the thinking roles, per engine, for comparing engines on cost."""
    out: dict[str, dict] = {}
    for result in session.scalars(select(Execution.result).where(Execution.run_id == run.id)):
        for call in (result or {}).get("_calls", []):
            total = out.setdefault(call["engine"], {"calls": 0, "attempts": 0, "seconds": 0.0, "cost_usd": 0.0})
            usage = call.get("usage") or {}
            total["calls"] += 1
            total["attempts"] += call.get("attempts") or 1
            total["seconds"] = round(total["seconds"] + (usage.get("seconds") or 0), 1)
            total["cost_usd"] = round(total["cost_usd"] + (usage.get("cost_usd") or 0), 4)
    return out


def score_case(session: Session, run: Run, golden: dict) -> dict:
    from controller.app.coordinator import artifact  # avoid an import cycle at module load
    plans = session.scalars(select(Plan).where(Plan.run_id == run.id).order_by(Plan.version)).all()
    questions = session.scalars(select(Clarification).where(Clarification.run_id == run.id)).all()
    spec = latest_document(session, run.id, "spec")
    result = {"case": run.eval_case, "run_id": str(run.id), "status": run.status, "reason": run.status_reason,
              "blocking_questions": [q.question for q in questions if q.blocking],
              "criteria": len(parse_criteria(spec.content)) if spec else 0,
              "plans": len(plans), "usage": _usage(session, run)}
    if not plans:
        return result
    policy = EffectivePolicy.model_validate_json(artifact(session, run.id, "effective_policy"))
    first, final = plans[0], plans[-1]
    candidate = _source_files({p for t in final.payload["tasks"] for p in t["modify"] + t["create"]}, policy)
    expected = _source_files({p for t in golden["tasks"] for p in t.get("allowed_paths", [])}, policy)
    hit = candidate & expected
    result.update({
        "grounding_v1": first.grounding_report["status"], "grounding_final": final.grounding_report["status"],
        "findings_v1": first.grounding_report["findings"], "findings_final": final.grounding_report["findings"],
        "regenerated": final.regenerated_from is not None,
        "tasks": len(final.payload["tasks"]), "golden_tasks": len(golden["tasks"]),
        "test_cases": len(final.test_plan["cases"]),
        "files": sorted(candidate), "golden_files": sorted(expected),
        "file_recall": round(len(hit) / len(expected), 2) if expected else None,
        "file_precision": round(len(hit) / len(candidate), 2) if candidate else None,
        "missed_files": sorted(expected - candidate), "extra_files": sorted(candidate - expected),
        "seconds_to_plan": round((final.created_at - run.created_at).total_seconds()),
    })
    return result


def summarize(cases: list[dict]) -> dict:
    planned = [c for c in cases if c.get("plans")]

    def rate(key: str) -> float | None:
        return round(sum(1 for c in planned if c.get(key) == "pass") / len(planned), 2) if planned else None

    def mean(key: str) -> float | None:
        values = [c[key] for c in planned if c.get(key) is not None]
        return round(sum(values) / len(values), 2) if values else None
    return {"cases": len(cases), "planned": len(planned),
            "asked_blocking_questions": sum(1 for c in cases if c.get("blocking_questions")),
            "grounding_pass_v1": rate("grounding_v1"), "grounding_pass_final": rate("grounding_final"),
            "file_recall": mean("file_recall"), "file_precision": mean("file_precision"),
            "mean_seconds_to_plan": mean("seconds_to_plan")}


def advance(session: Session) -> list[uuid.UUID]:
    """Finish every running eval whose cases all reached the plan gate or stopped. Returns finished ids."""
    finished = []
    for evaluation in session.scalars(select(EvalRun).where(EvalRun.status == "running").with_for_update(skip_locked=True)):
        runs = session.scalars(select(Run).where(Run.eval_run_id == evaluation.id).order_by(Run.eval_case)).all()
        if not runs or any(RunStatus(run.status) not in CASE_DONE for run in runs):
            continue
        suite = load_suite(evaluation.suite)
        golden = {case.id: case.golden for case in suite.cases}
        cases = [score_case(session, run, golden[run.eval_case]) for run in runs]
        evaluation.results = {"summary": summarize(cases), "cases": cases}
        evaluation.status, evaluation.finished_at = "completed", datetime.now(timezone.utc)
        for run in runs:
            if run.status not in (RunStatus.CANCELLED, RunStatus.FAILED):
                services.cancel(session, services.get_run(session, run.id), actor=ACTOR,
                                reason="eval finished; experiment runs never reach coding")
        record_event(session, "eval.completed", actor=ACTOR, payload={"eval": str(evaluation.id),
                                                                      "summary": evaluation.results["summary"]})
        finished.append(evaluation.id)
    return finished


def eval_view(session: Session, evaluation: EvalRun, with_cases: bool = True) -> dict:
    runs = session.scalars(select(Run).where(Run.eval_run_id == evaluation.id).order_by(Run.eval_case)).all()
    baseline = session.get(EvalRun, evaluation.baseline_id) if evaluation.baseline_id else None
    view = {"id": str(evaluation.id), "suite": evaluation.suite, "scope": evaluation.scope,
            "trigger": evaluation.trigger, "status": evaluation.status, "fingerprint": evaluation.fingerprint,
            "created_at": evaluation.created_at.isoformat(),
            "finished_at": evaluation.finished_at.isoformat() if evaluation.finished_at else None,
            "summary": (evaluation.results or {}).get("summary"),
            "progress": [{"case": run.eval_case, "run_id": str(run.id), "status": run.status} for run in runs],
            "baseline": None if baseline is None else {
                "id": str(baseline.id), "summary": (baseline.results or {}).get("summary"),
                "fingerprint": baseline.fingerprint, "created_at": baseline.created_at.isoformat()}}
    if with_cases:
        view["cases"] = (evaluation.results or {}).get("cases", [])
    return view
