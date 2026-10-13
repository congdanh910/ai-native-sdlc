"""Verify GitHub webhook signatures and apply one required CI check idempotently."""

import hashlib
import hmac
import json
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from controller.app.models import PullRequest, WebhookDelivery
from controller.app.state_machine import event


MAX_WEBHOOK_BYTES = 1_000_000


class WebhookAuthError(Exception):
    pass


class WebhookInvalidError(Exception):
    pass


def apply_github_webhook(
    session: Session, *, secret: str, required_check: str,
    delivery_id: str, event_type: str, signature: str, body: bytes,
) -> dict:
    if not secret:
        raise WebhookAuthError("GitHub webhook secret is not configured")
    if len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookInvalidError("webhook body exceeds limit")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature or ""):
        raise WebhookAuthError("invalid webhook signature")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookAuthError("webhook signature mismatch")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery_id or ""):
        raise WebhookInvalidError("invalid delivery ID")
    if not re.fullmatch(r"[a-z_]{1,80}", event_type or ""):
        raise WebhookInvalidError("invalid event type")
    digest = hashlib.sha256(body).hexdigest()
    existing = session.get(WebhookDelivery, delivery_id)
    if existing is not None:
        if existing.payload_sha256 != digest or existing.event_type != event_type:
            raise WebhookInvalidError("delivery ID reused with different payload")
        return {"status": "duplicate"}
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WebhookInvalidError("webhook body is not a JSON object") from exc
    try:
        with session.begin_nested():
            session.add(WebhookDelivery(
                delivery_id=delivery_id, event_type=event_type, payload_sha256=digest,
            ))
            session.flush()
    except IntegrityError:
        existing = session.get(WebhookDelivery, delivery_id)
        if existing is not None and existing.payload_sha256 == digest and existing.event_type == event_type:
            return {"status": "duplicate"}
        raise WebhookInvalidError("delivery ID reused with different payload")
    if event_type != "check_run" or payload.get("action") != "completed":
        return {"status": "ignored"}
    try:
        repo = payload["repository"]["full_name"]
        check = payload["check_run"]
        name = check["name"]
        head_sha = check["head_sha"]
        conclusion = check["conclusion"]
    except (KeyError, TypeError) as exc:
        raise WebhookInvalidError("check_run payload is incomplete") from exc
    if name != required_check:
        return {"status": "ignored"}
    if not isinstance(repo, str) or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", str(head_sha)):
        raise WebhookInvalidError("check_run repository or head SHA is invalid")
    linked = [
        item for item in check.get("pull_requests", [])
        if isinstance(item, dict) and isinstance(item.get("number"), int)
        and isinstance(item.get("head"), dict)
        and re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", str(item["head"].get("sha", "")))
    ]
    candidate_numbers = {item["number"] for item in linked}
    if candidate_numbers:
        pull_requests = session.scalars(
            select(PullRequest).where(
                PullRequest.repo_full_name == repo,
                PullRequest.number.in_(candidate_numbers),
            )
        ).all()
        pull_requests = [pr for pr in pull_requests if any(
            item["number"] == pr.number and item["head"]["sha"] == pr.head_sha
            for item in linked
        )]
    else:
        pull_requests = session.scalars(
            select(PullRequest).where(PullRequest.repo_full_name == repo, PullRequest.head_sha == head_sha)
        ).all()
    for pull_request in pull_requests:
        pull_request.ci_status = "success" if conclusion == "success" else "failure"
        event(session, pull_request.run_id, "ci.updated", {
            "delivery_id": delivery_id, "check": name,
            "conclusion": conclusion, "status": pull_request.ci_status,
        })
    return {"status": "updated" if pull_requests else "unmatched", "runs": len(pull_requests)}
