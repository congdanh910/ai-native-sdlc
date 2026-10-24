"""Create .env from .env.example with generated secrets."""

import base64
from pathlib import Path
import re
import secrets

OPERATOR = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def render_env(template: str, values: dict[str, str]) -> str:
    """Fill the template: explicit values first, then generated secrets.

    Secrets use URL-safe characters because some of them end up in database URLs.
    """
    lines = []
    for line in template.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            if key in values:
                value = values[key]
            elif value == "CHANGE_ME_SECRET":
                value = _fernet_key() if key == "LANGFLOW_SECRET_KEY" else secrets.token_urlsafe(32)
            line = f"{key}={value}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def write_env(path: Path, template_path: Path, values: dict[str, str]) -> None:
    if path.exists():
        raise FileExistsError(f"{path.name} already exists; edit it instead of regenerating secrets")
    operator = values.get("SDLC_OPERATOR", "")
    if not OPERATOR.fullmatch(operator):
        raise ValueError("operator must start with a letter and use letters, digits, '.', '_' or '-'")
    content = render_env(template_path.read_text(), values)
    path.touch(mode=0o600)
    path.write_text(content)
