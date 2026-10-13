import uuid

from sqlalchemy.orm import Session

from controller.app.models import Event


def record_event(session: Session, event_type: str, *, actor: str, acting_role: str | None = None,
                 run_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None,
                 payload: dict | None = None) -> Event:
    event = Event(event_type=event_type, actor=actor, acting_role=acting_role, run_id=run_id,
                  project_id=project_id, payload=payload or {})
    session.add(event)
    return event
