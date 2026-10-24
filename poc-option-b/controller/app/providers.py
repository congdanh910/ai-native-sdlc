"""AI providers: which engine each role uses, in order, and which providers are out of their subscription window.

The Platform Admin lists each role's engines in order of preference (the AI providers page). When a provider's
subscription window is used up, it is marked limited until the time it gives ("try again at 7:44 PM") or for
subscription_wait_seconds, and the role moves to its next engine in the same job; the job waits only when every
engine of the role is limited. Codex and Claude are one subscription each, shared by their read-only (thinking) and
working (doing) sessions. Without settings, .env decides as before (SDLC_ENGINES, the project's executors).
"""

from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select

from controller.app.config import get_settings
from controller.app.db import session_scope
from controller.app.events import record_event
from controller.app.models import EngineLimit, Execution, ProviderSetting

THINKING_ROLES = ("business_analyst", "tech_lead", "qa_analyst", "code_reviewer")
DOING_ROLES = ("qa_engineer", "developer")
THINKING_ENGINES = ("codex-readonly", "claude-readonly")
DOING_ENGINES = ("codex", "claude")
# "try again at 7:44 PM" (Codex), "resets 7:40pm (Asia/Saigon)" (Claude)
MODEL = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,80}$")
# Codex's model_reasoning_effort when its model cache cannot be read; empty: the model's default
EFFORTS = ("", "low", "medium", "high", "xhigh", "max", "ultra")
# what the page offers; any name the CLI accepts may be typed
MODEL_SUGGESTIONS = {"claude": ["opus", "sonnet", "haiku", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                                "claude-fable-5-1"],
                     "codex": ["gpt-6-sol"]}  # when the Codex model cache cannot be read
RESET_AT = re.compile(r"(?:try again at|resets(?: at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([AP]M)?", re.IGNORECASE)


def provider_of(engine: str) -> str:
    """codex-readonly and codex share one subscription."""
    return engine.removesuffix("-readonly")


def load() -> dict:
    """{"thinking": {role: [engines]}, "doing": {role: [executors]}, "fallback": bool}; empty lists: .env decides."""
    with session_scope() as session:
        row = session.get(ProviderSetting, "default")
        data = dict(row.data) if row else {}
    return {"thinking": data.get("thinking") or {}, "doing": data.get("doing") or {},
            "fallback": data.get("fallback", True), "models": data.get("models") or {}}


def save(data: dict, *, actor: str) -> dict:
    thinking = {r: [e for e in (data.get("thinking") or {}).get(r, []) if e] for r in THINKING_ROLES}
    doing = {r: [e for e in (data.get("doing") or {}).get(r, []) if e] for r in DOING_ROLES}
    for role, engines in thinking.items():
        bad = [e for e in engines if e not in THINKING_ENGINES]
        if bad or len(set(engines)) != len(engines):
            raise ValueError(f"{role}: use each of {', '.join(THINKING_ENGINES)} at most once (got {', '.join(engines)})")
    for role, engines in doing.items():
        bad = [e for e in engines if e not in DOING_ENGINES]
        if bad or len(set(engines)) != len(engines):
            raise ValueError(f"{role}: use each of {', '.join(DOING_ENGINES)} at most once (got {', '.join(engines)})")
    models: dict = {}
    for role, per in (data.get("models") or {}).items():
        if role not in THINKING_ROLES + DOING_ROLES:
            raise ValueError(f"unknown role {role}")
        for provider, choice in (per or {}).items():
            if provider not in ("codex", "claude"):
                raise ValueError(f"unknown provider {provider}")
            model, effort = str(choice.get("model", "")).strip(), str(choice.get("effort", "")).strip()
            if model and not MODEL.fullmatch(model):
                raise ValueError(f"{role} on {provider}: {model!r} is not a model name")
            allowed = efforts()
            supported = next((m["efforts"] for m in codex_models() if m["slug"] == model), None)
            if effort and provider != "codex":
                raise ValueError(f"{role} on {provider}: a reasoning effort is for Codex only")
            if effort and (effort not in allowed or (supported is not None and effort not in supported)):
                raise ValueError(f"{role} on {provider}: effort {effort!r} is not one of "
                                 f"{', '.join(supported or [e for e in allowed if e])}")
            if model or effort:
                models.setdefault(role, {})[provider] = {"model": model, "effort": effort}
    value = {"thinking": thinking, "doing": doing, "fallback": bool(data.get("fallback", True)), "models": models}
    with session_scope() as session:
        row = session.get(ProviderSetting, "default")
        if row is None:
            session.add(ProviderSetting(key="default", data=value, updated_by=actor))
        else:
            row.data, row.updated_by = value, actor
        record_event(session, "providers.saved", actor=actor, acting_role="platform_admin", payload=value)
    return value


def limits(now: datetime | None = None) -> dict[str, dict]:
    """Providers out of quota now: {provider: {until, reason}}."""
    now = now or datetime.now(timezone.utc)
    with session_scope() as session:
        return {row.provider: {"until": row.limited_until, "reason": row.reason}
                for row in session.scalars(select(EngineLimit)) if row.limited_until > now}


def reset_time(message: str, now: datetime | None = None) -> datetime:
    """When the provider says it is back ("try again at 7:44 PM", the machine's local time), else after
    subscription_wait_seconds. The time is the nearest such clock time: one that has just passed (the message came
    seconds before the reset, or the reset is late) means trying again after subscription_wait_seconds, not tomorrow."""
    now = now or datetime.now(timezone.utc)
    wait = now + timedelta(seconds=get_settings().subscription_wait_seconds)
    found = RESET_AT.search(message or "")
    if found:
        hour, minute, half = int(found[1]), int(found[2] or 0), (found[3] or "").upper()
        hour = hour % 12 + (12 if half == "PM" else 0) if half else hour
        local = now.astimezone()
        today = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        at = min((today + timedelta(days=d) for d in (-1, 0, 1)), key=lambda t: abs(t - local))
        return at.astimezone(timezone.utc) if at > local else wait
    return wait


def mark_limited(engine: str, message: str) -> datetime:
    provider = provider_of(engine)
    until = reset_time(message)
    with session_scope() as session:
        row = session.get(EngineLimit, provider)
        if row is None:
            session.add(EngineLimit(provider=provider, limited_until=until, reason=message[-500:]))
        else:
            row.limited_until, row.reason = until, message[-500:]
        record_event(session, "provider.limited", actor="delivery_coordinator",
                     payload={"provider": provider, "until": until.isoformat(), "reason": message[-300:]})
    return until


def mark_available(provider: str, *, actor: str) -> int:
    """The quota was reset: the provider is used again at once, and the jobs that waited for it run now."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(EngineLimit, provider)
        if row is not None:
            session.delete(row)
        waiting = session.scalars(select(Execution).where(Execution.status == "queued", Execution.available_at > now,
                                                          Execution.error.like("SubscriptionLimit%"))).all()
        for execution in waiting:
            execution.available_at = now
        record_event(session, "provider.available", actor=actor, acting_role="platform_admin",
                     payload={"provider": provider, "jobs": len(waiting)})
        return len(waiting)


def chain(kind: str, role: str, first: str, allowed: list[str] | None = None) -> list[str]:
    """The engines to try for a role, in order: the Admin's list when set (else the run's or project's engine), then,
    with fallback on, the other engines of that kind; only those `allowed` (a project's executors)."""
    settings = load()
    every = THINKING_ENGINES if kind == "thinking" else DOING_ENGINES
    listed = settings[kind].get(role) or [first]
    order = list(dict.fromkeys(listed + ([e for e in every if e not in listed] if settings["fallback"] else [])))
    if first not in every:  # qwen3, mock: not a subscription; nothing to fall back between
        return [first]
    return [e for e in order if allowed is None or e in allowed]


def available(engines: list[str]) -> tuple[list[str], datetime | None]:
    """The engines not limited now, and when the first limited one is back."""
    out = limits()
    free = [e for e in engines if provider_of(e) not in out]
    back = min((out[provider_of(e)]["until"] for e in engines if provider_of(e) in out), default=None)
    return free, back


def codex_models() -> list[dict]:
    """The models this Codex login may use, with the reasoning efforts each one supports, from the CLI's cache
    (SDLC_CODEX_MODELS_CACHE); [] when it cannot be read."""
    import json
    from pathlib import Path
    try:
        data = json.loads(Path(get_settings().sdlc_codex_models_cache).expanduser().read_text())
    except (OSError, ValueError):
        return []
    return [{"slug": m["slug"], "name": m.get("display_name") or m["slug"],
             "efforts": [level.get("effort") for level in m.get("supported_reasoning_levels") or [] if level.get("effort")],
             "default_effort": m.get("default_reasoning_level") or ""}
            for m in data.get("models") or [] if isinstance(m, dict) and m.get("slug") and m.get("visibility") != "hide"]


def efforts() -> tuple[str, ...]:
    known = {e for m in codex_models() for e in m["efforts"]}
    return ("",) + tuple(sorted(known, key=lambda e: list(EFFORTS).index(e) if e in EFFORTS else 99)) if known else EFFORTS


def model_for(role: str, engine: str) -> tuple[str, str]:
    """The model and reasoning effort the Admin chose for a role on a provider; ("", "") keeps the CLI's default."""
    choice = (load()["models"].get(role) or {}).get(provider_of(engine)) or {}
    return choice.get("model", ""), choice.get("effort", "")


def recommendations() -> dict:
    """Suggested model and effort per role (provider-recommendations.json at the platform root), shown on the page."""
    import json
    from controller.app.config import PROJECT_ROOT
    try:
        data = json.loads((PROJECT_ROOT / "provider-recommendations.json").read_text())
    except (OSError, ValueError):
        return {}
    return {role: value for role, value in data.items() if role in THINKING_ROLES + DOING_ROLES}
