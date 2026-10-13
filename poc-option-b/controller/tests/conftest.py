import pytest

from controller.app.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    identity = tmp_path / "id_test"
    identity.write_text("not a real key")
    return Settings(
        _env_file=None,
        sdlc_operator="tester",
        sdlc_db_password="pw",
        langflow_api_key="key",
        gx10_ssh_host="cdp@gx10.example",
        gx10_ssh_port=2222,
        gx10_ssh_identity_file=str(identity),
    )
