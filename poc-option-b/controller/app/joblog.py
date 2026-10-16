"""A live log per job, so the control room can follow a role while it works.

The worker opens the log when it claims a job; the coordinator writes milestones (which engine, which
attempt, why an answer was rejected) and every subprocess streams its output here line by line. JSON
events from the Claude and Codex CLIs are summarised to one readable line each. Secrets are redacted.
"""

from contextvars import ContextVar
from datetime import datetime
import json
from pathlib import Path
import threading
import uuid

from controller.app.config import DATA_DIR, get_settings

_current: ContextVar[Path | None] = ContextVar("job_log", default=None)
_lock = threading.Lock()


def path_for(run_id: uuid.UUID | str, execution_id: uuid.UUID | str) -> Path:
    return DATA_DIR / "logs" / str(run_id) / "jobs" / f"{execution_id}.log"


def start(run_id, execution_id, header: str) -> Path:
    path = path_for(run_id, execution_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _current.set(path)
    _append(path, f"[{_now()}] {header}\n")
    return path


def stop() -> None:
    _current.set(None)


def current() -> Path | None:
    return _current.get()


def note(text: str) -> None:
    """A milestone of the running job, such as 'Tech Lead: calling claude-readonly, attempt 1'."""
    path = _current.get()
    if path is not None:
        _append(path, f"[{_now()}] {_redact(text)}\n")


def stream(path: Path | None, program: str, line: str) -> None:
    """One line of a subprocess's output, summarised when it is a CLI JSON event."""
    if path is None or not line.strip():
        return
    text = summarise(line.rstrip("\n"))
    if text:
        _append(path, f"[{_now()}] {program}: {_redact(text)}\n")


def read(run_id, execution_id, offset: int = 0) -> tuple[str, int]:
    path = path_for(run_id, execution_id)
    if not path.exists():
        return "", 0
    with path.open("rb") as handle:
        handle.seek(max(0, offset))
        data = handle.read(get_settings().job_log_read_bytes)
    return data.decode("utf-8", errors="replace"), max(0, offset) + len(data)


def summarise(line: str) -> str | None:
    try:
        event = json.loads(line)
    except ValueError:
        return line[:500]
    if not isinstance(event, dict):
        return line[:500]
    kind = event.get("type")
    # Claude CLI (--output-format stream-json)
    if kind == "system":
        return f"session started ({event.get('model', 'model unknown')})" if event.get("subtype") == "init" else None
    if kind == "assistant":
        parts = []
        for item in (event.get("message") or {}).get("content") or []:
            if item.get("type") == "tool_use":
                arguments = item.get("input") or {}
                target = (arguments.get("file_path") or arguments.get("path") or arguments.get("pattern")
                          or arguments.get("command") or "")
                parts.append(f"{item.get('name')} {str(target)[:200]}".strip())
            elif item.get("type") == "text" and item.get("text", "").strip():
                parts.append("says: " + " ".join(item["text"].split())[:300])
        return "; ".join(parts) or None
    if kind == "user":
        return None  # tool results: too long to show, the next assistant step shows what came of them
    if kind == "result":
        return (f"finished ({'error' if event.get('is_error') else 'ok'}): {event.get('num_turns')} turns, "
                f"{round((event.get('duration_ms') or 0) / 1000)}s, API-price estimate ${event.get('total_cost_usd') or 0:.2f} (subscription usage, not billed)")
    # Codex CLI (--json)
    item = event.get("item") if isinstance(event.get("item"), dict) else None
    if item:
        if item.get("type") == "command_execution":
            state = "runs" if kind == "item.started" else f"ran (exit {item.get('exit_code')})"
            return f"{state} {str(item.get('command'))[:200]}"
        if item.get("type") in ("agent_message", "reasoning") and kind == "item.completed":
            return f"{'says' if item['type'] == 'agent_message' else 'thinks'}: {' '.join(str(item.get('text', '')).split())[:300]}"
        if item.get("type") == "file_change" and kind == "item.completed":
            return "changes " + ", ".join(c.get("path", "?") for c in item.get("changes") or [])
        return None
    if kind == "turn.completed":
        return f"turn completed, tokens {json.dumps(event.get('usage') or {})[:200]}"
    if kind in ("thread.started", "turn.started"):
        return kind.replace(".", " ")
    return None


def _append(path: Path, text: str) -> None:
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)


def _redact(text: str) -> str:
    from controller.app.doing.process import redact  # avoid a cycle at import time
    return redact(text)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
