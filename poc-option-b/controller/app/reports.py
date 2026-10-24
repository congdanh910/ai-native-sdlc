"""Team report: what each role produced, where humans corrected it, and what each engine cost.

Computed from the controller's records only (runs, documents, plans, executions,
approvals, events, evals). Normal runs and experiment runs are reported apart:
role metrics use normal runs, engine comparisons use evals.
"""

from collections import Counter, defaultdict
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.models import (Approval, Artifact, Clarification, Document, EvalRun, Event, Execution, Plan,
                                   Project, PullRequest, Run)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


def team_report(session: Session) -> dict:
    runs = session.scalars(select(Run).where(Run.experiment.is_(False))).all()
    run_ids = [run.id for run in runs]
    projects = {p.id: p.key for p in session.scalars(select(Project))}
    executions = session.scalars(select(Execution).where(Execution.run_id.in_(run_ids))).all() if run_ids else []
    plans = session.scalars(select(Plan).where(Plan.run_id.in_(run_ids)).order_by(Plan.version)).all() if run_ids else []
    approvals = session.scalars(select(Approval).where(Approval.run_id.in_(run_ids))).all() if run_ids else []
    documents = session.scalars(select(Document).where(Document.run_id.in_(run_ids))).all() if run_ids else []
    questions = session.scalars(select(Clarification).where(Clarification.run_id.in_(run_ids))).all() if run_ids else []
    engines_by_run = {a.run_id: json.loads(a.content) for a in session.scalars(
        select(Artifact).where(Artifact.kind == "engines", Artifact.run_id.in_(run_ids)))} if run_ids else {}

    steps = [(e, s) for e in executions if e.kind == "code_task" for s in (e.result or {}).get("steps", [])]
    plans_by_run = defaultdict(list)
    for plan in plans:
        plans_by_run[plan.run_id].append(plan)
    ai_plans = [p for p in plans if p.author_kind == "ai"]
    approved_plans = [a for a in approvals if a.stage == "plan" and a.decision == "approve"]
    approved_ai = sum(1 for a in approved_plans if any(p.id == a.plan_id and p.author_kind == "ai" for p in plans))
    first_plans = [ps[0] for ps in plans_by_run.values() if ps]
    reviews = [d for d in documents if d.kind == "review" and d.author_kind == "ai"]
    pulls = session.scalars(select(PullRequest).where(PullRequest.run_id.in_(run_ids))).all() if run_ids else []
    rulings = session.scalars(select(Event).where(Event.event_type == "dispute.ruled",
                                                  Event.run_id.in_(run_ids))).all() if run_ids else []

    def step_count(state: str, ok: bool | None = None, text: str | None = None) -> int:
        return sum(1 for _, s in steps if s["state"] == state and (ok is None or s["ok"] == ok)
                   and (text is None or text in s["detail"]))

    task_results = defaultdict(list)
    for execution in executions:
        if execution.kind == "code_task" and execution.result:
            task_results[execution.task_id].append(execution.result.get("steps", []))
    first_time = sum(1 for attempts in task_results.values()
                     if any(s["state"] == "VALIDATING" and s["ok"] for s in attempts[0])
                     and not any(s["state"] == "REMEDIATING" for s in attempts[0]))

    roles = {
        "business_analyst": {
            "specs": sum(1 for d in documents if d.kind == "spec" and d.author_kind == "ai"),
            "blocking_questions": sum(1 for q in questions if q.blocking),
            "specs_revised_by_humans": sum(1 for d in documents if d.kind == "spec" and d.author_kind == "human"),
        },
        "tech_lead": {
            "plans": len(ai_plans),
            "first_plans_grounded": _ratio(sum(1 for p in first_plans if (p.grounding_report or {}).get("status")
                                               == "pass"), len(first_plans)),
            "plans_approved_without_edits": approved_ai,
            "plans_revised_by_humans": sum(1 for p in plans if p.author_kind == "human"),
            "mean_plan_versions_per_run": _ratio(len(plans), len(plans_by_run)),
        },
        "qa_analyst": {
            "test_cases": sum(len(p.test_plan.get("cases", [])) for p in ai_plans),
            "criteria_without_test_case": sum(1 for p in ai_plans for f in (p.grounding_report or {}).get("findings", [])
                                              if f.startswith("no test case proves")),
        },
        "qa_engineer": {
            "test_sessions": step_count("WRITING_TESTS"),
            "rejected_test_changes": step_count("WRITING_TESTS", False),
            "red_check_failures": step_count("RED_CHECK", False),
            "disputes_ruled": len(rulings),
            "tests_updated_by_ruling": sum(1 for r in rulings if r.payload.get("decision") == "amend_tests"),
        },
        "developer": {
            "coding_sessions": step_count("CODING") + step_count("REMEDIATING"),
            "tasks_passing_validation_first_time": first_time,
            "tasks_attempted": len(task_results),
            "remediation_attempts": step_count("REMEDIATING"),
            "path_violations": step_count("CODING", False, "code changes rejected")
            + step_count("REMEDIATING", False, "code changes rejected"),
            "disputes": sum(1 for _, s in steps if s["dispute"]),
        },
        "validation_gate": {
            "validations": step_count("VALIDATING"),
            "validation_failures": step_count("VALIDATING", False),
            "weak_tests_classified": sum(1 for r in runs if r.failure_category == "WEAK_TESTS"),
        },
        "code_reviewer": {
            "reviews": len(reviews),
            "verdict_concerns": sum(1 for d in reviews if (d.data or {}).get("verdict") == "concerns"),
            "important_findings": sum(1 for d in reviews for f in (d.data or {}).get("findings", [])
                                      if f.get("severity") == "important"),
        },
        "release_engineer": {
            "draft_prs": sum(1 for p in pulls if not p.simulated and p.number),
            "ci_success": sum(1 for p in pulls if p.ci_status == "success"),
            "merged": sum(1 for p in pulls if p.merged_sha and not p.simulated),
        },
    }

    engines = defaultdict(lambda: {"calls": 0, "seconds": 0.0, "cost_usd": 0.0})
    for execution in executions:
        for call in (execution.result or {}).get("_calls", []):
            usage = call.get("usage") or {}
            total = engines[call["engine"]]
            total["calls"] += 1
            total["seconds"] = round(total["seconds"] + (usage.get("seconds") or 0), 1)
            total["cost_usd"] = round(total["cost_usd"] + (usage.get("cost_usd") or 0), 4)
        for step in (execution.result or {}).get("steps", []):
            evidence = step.get("evidence") or {}
            if evidence.get("executor"):
                total = engines[evidence["executor"]]
                total["calls"] += 1
                total["seconds"] = round(total["seconds"] + (evidence.get("seconds") or 0), 1)
                total["cost_usd"] = round(total["cost_usd"] + ((evidence.get("usage") or {}).get("cost_usd") or 0), 4)

    evals = [{"id": str(e.id), "suite": e.suite, "created_at": e.created_at.isoformat(), "summary": (e.results or {}).get("summary"),
              "engines": (e.fingerprint or {}).get("engines")}
             for e in session.scalars(select(EvalRun).where(EvalRun.status == "completed")
                                      .order_by(EvalRun.created_at))]

    return {
        "runs": {"total": len(runs), "by_status": dict(Counter(r.status for r in runs)),
                 "by_project": dict(Counter(projects.get(r.project_id, "?") for r in runs)),
                 "human_corrected": sum(1 for r in runs if r.human_corrected),
                 "failure_categories": dict(Counter(r.failure_category for r in runs if r.failure_category)),
                 "engines": dict(Counter(json.dumps(e, sort_keys=True) for e in engines_by_run.values()))},
        "roles": roles,
        "engines": dict(engines),
        "evals": evals,
    }
