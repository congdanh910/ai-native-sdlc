from controller.app.config import Settings


def test_database_url_is_derived_from_password_and_port(settings):
    assert settings.database_url == "postgresql+psycopg://sdlc:pw@127.0.0.1:55442/sdlc"


def test_database_url_override_wins():
    settings = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://x@y/z")
    assert settings.database_url == "postgresql+psycopg://x@y/z"


def test_placeholders_are_reported():
    settings = Settings(_env_file=None, sdlc_operator="me")
    assert "SDLC_DB_PASSWORD" in settings.placeholders()
    assert "SDLC_OPERATOR" not in settings.placeholders()


def test_gx10_problems(settings, tmp_path):
    assert settings.gx10_problems() == []
    bad = settings.model_copy(update={"gx10_ssh_host": "-oProxyCommand=x",
                                      "gx10_ssh_identity_file": str(tmp_path / "missing")})
    assert len(bad.gx10_problems()) == 2


def test_langflow_reaches_tunnel_through_docker_host(settings):
    assert settings.ollama_url_from_langflow == "http://host.docker.internal:11435"
