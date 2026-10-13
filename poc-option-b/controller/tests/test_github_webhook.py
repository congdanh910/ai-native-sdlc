import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from controller.app.integrations.github_webhook import (
    WebhookAuthError, WebhookInvalidError, apply_github_webhook,
)
from controller.app.models import Base, Event, PullRequest, Run, WebhookDelivery
from controller.app.schemas import RunCreate, VersionedDecision
from controller.app.services import approve_pr, create_run, record_draft_pr
from controller.app.state_machine import ConflictError


def test_signed_ci_webhook_replay_and_human_approval(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ci.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = create_run(session, RunCreate(
            repo_id="owner/repo", base_commit="a" * 40,
            requirement="Add search", idempotency_key="ci-demo",
        ), frozenset({"owner/repo"}))
        run.status = "PR_READY"
        session.flush()
        pull_request = record_draft_pr(
            session, run.id, repo_full_name="owner/repo", number=7,
            url="https://github.com/owner/repo/pull/7", head_sha="b" * 40,
        )
        session.commit()
        assert run.status == "WAITING_PR_APPROVAL"
        with pytest.raises(ConflictError, match="CI"):
            approve_pr(session, run.id, VersionedDecision(
                expected_run_version=run.version, actor="human",
            ))

        payload = {
            "action": "completed", "repository": {"full_name": "owner/repo"},
            "check_run": {"name": "task-board-tests", "head_sha": "b" * 40, "conclusion": "success"},
        }
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        kwargs = dict(
            secret="test-secret", required_check="task-board-tests",
            delivery_id=str(uuid4()), event_type="check_run",
            signature=signature, body=body,
        )
        with pytest.raises(WebhookAuthError):
            apply_github_webhook(session, **{**kwargs, "signature": "sha256=" + "0" * 64})
        assert apply_github_webhook(session, **kwargs) == {"status": "updated", "runs": 1}
        session.commit()
        assert apply_github_webhook(session, **kwargs) == {"status": "duplicate"}
        with pytest.raises(WebhookInvalidError, match="reused"):
            changed = b"{}"
            apply_github_webhook(session, **{
                **kwargs, "body": changed,
                "signature": "sha256=" + hmac.new(b"test-secret", changed, hashlib.sha256).hexdigest(),
            })
        assert session.get(PullRequest, pull_request.id).ci_status == "success"
        merge_payload = {
            **payload,
            "check_run": {
                **payload["check_run"], "head_sha": "c" * 40,
                "pull_requests": [{"number": 7, "head": {"sha": "b" * 40}}],
            },
        }
        merge_body = json.dumps(merge_payload).encode()
        assert apply_github_webhook(session, **{
            **kwargs, "delivery_id": str(uuid4()), "body": merge_body,
            "signature": "sha256=" + hmac.new(b"test-secret", merge_body, hashlib.sha256).hexdigest(),
        }) == {"status": "updated", "runs": 1}
        assert session.get(WebhookDelivery, kwargs["delivery_id"]) is not None
        approve_pr(session, run.id, VersionedDecision(
            expected_run_version=run.version, actor="human",
        ))
        session.commit()
        assert run.status == "COMPLETED"
        assert any(item.event_type == "ci.updated" for item in session.scalars(select(Event)))
    engine.dispose()
