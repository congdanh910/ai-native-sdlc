"""Exercise the B2 state machine and worker with an isolated in-memory database."""

import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from controller.app.models import Base, Event, Execution, Task
from controller.app.schemas import PlanDecision, PlanPayload, RunCreate
from controller.app.services import approve_plan, create_run, record_plan
from controller.app.state_machine import transition
from controller.app.worker import claim_next_execution, complete_mock_execution


def main() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = create_run(
            session,
            RunCreate(
                repo_id="demo/repo", base_commit="a" * 40,
                requirement="Demonstrate durable planning and task ordering",
                idempotency_key="b2-demo",
            ),
            frozenset({"demo/repo"}),
        )
        transition(session, run, "ANALYZING")
        transition(session, run, "PLANNING")
        plan = record_plan(
            session,
            run.id,
            PlanPayload.model_validate({"tasks": [
                {"id": "implement", "description": "Implement change", "allowed_paths": ["src"],
                 "acceptance_criteria": ["Feature works"]},
                {"id": "document", "description": "Document change", "dependencies": ["implement"],
                 "allowed_paths": ["docs"], "acceptance_criteria": ["Guide updated"]},
            ]}),
        )
        waiting_version = run.version
        approve_plan(
            session,
            run.id,
            PlanDecision(
                plan_id=plan.id, plan_sha256=plan.sha256,
                expected_run_version=waiting_version, actor="demo-human",
            ),
        )
        processed: list[str] = []
        while execution := claim_next_execution(session, "demo-worker"):
            task = session.get(Task, execution.task_id)
            processed.append(task.external_task_id)
            complete_mock_execution(session, execution.id, "demo-worker")
        session.commit()
        assert run.status == "VALIDATING"
        assert processed == ["implement", "document"]
        assert session.scalar(select(func.count()).select_from(Execution)) == 2
        print(json.dumps({
            "run_id": str(run.id), "status": run.status, "run_version": run.version,
            "plan_sha256": plan.sha256, "processed_tasks": processed,
            "event_count": session.scalar(select(func.count()).select_from(Event)),
        }, indent=2))
    engine.dispose()


if __name__ == "__main__":
    main()
