import base64

import pytest

from cli.envfile import render_env, write_env
from controller.app.config import PROJECT_ROOT

TEMPLATE = (PROJECT_ROOT / ".env.example").read_text()


def parse(content: str) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in content.splitlines() if "=" in line and not line.startswith("#"))
    return dict(pairs)


def test_render_fills_secrets_and_keeps_explicit_values():
    values = parse(render_env(TEMPLATE, {"SDLC_OPERATOR": "danh", "GX10_SSH_PORT": "2222"}))
    assert values["SDLC_OPERATOR"] == "danh"
    assert values["GX10_SSH_PORT"] == "2222"
    assert values["SDLC_DB_PASSWORD"] != values["LANGFLOW_DB_PASSWORD"]
    assert all("CHANGE_ME_SECRET" not in value for value in values.values())
    assert len(base64.urlsafe_b64decode(values["LANGFLOW_SECRET_KEY"])) == 32


def test_render_keeps_comments():
    rendered = render_env(TEMPLATE, {})
    assert "# Never commit .env." in rendered


def test_write_refuses_to_overwrite(tmp_path):
    target = tmp_path / ".env"
    target.write_text("keep")
    with pytest.raises(FileExistsError):
        write_env(target, PROJECT_ROOT / ".env.example", {"SDLC_OPERATOR": "danh"})
    assert target.read_text() == "keep"


def test_write_validates_operator_and_sets_private_mode(tmp_path):
    with pytest.raises(ValueError):
        write_env(tmp_path / ".env", PROJECT_ROOT / ".env.example", {"SDLC_OPERATOR": "bad name"})
    target = tmp_path / ".env"
    write_env(target, PROJECT_ROOT / ".env.example", {"SDLC_OPERATOR": "danh"})
    assert target.stat().st_mode & 0o777 == 0o600
