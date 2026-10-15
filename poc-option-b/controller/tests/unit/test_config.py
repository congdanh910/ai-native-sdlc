from controller.app.config import PROJECT_ROOT, Settings


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



def project_yaml(**limits) -> str:
    import yaml
    data = yaml.safe_load((PROJECT_ROOT / "examples/task-board/.sdlc/project.yaml").read_text())
    data["limits"] = {**(data.get("limits") or {}), **limits}
    return yaml.safe_dump(data)


def test_project_limits_reach_the_effective_policy():
    from controller.app.doing.validation import Resources, container_args
    from controller.app.projects.config import effective_policy, parse_project_config
    policy = effective_policy(parse_project_config(project_yaml(
        session_timeout_minutes=45, thinking_timeout_minutes=15, setup_timeout_minutes=30, max_remediations=3,
        max_format_retries=1, container={"cpus": 4, "memory_mb": 4096, "pids": 1024, "tmp_mb": 512})))
    assert (policy.session_timeout_seconds, policy.thinking_timeout_seconds, policy.setup_timeout_seconds) == (2700, 900, 1800)
    assert (policy.max_remediations, policy.max_format_retries) == (3, 1)
    args = container_args("node:20-alpine", PROJECT_ROOT, ["node", "--test"], name="x",
                          resources=Resources(policy.container_cpus, policy.container_memory_mb, policy.container_pids,
                                              policy.container_tmp_mb))
    assert args[args.index("--cpus") + 1] == "4" and args[args.index("--memory") + 1] == "4096m"
    assert args[args.index("--pids-limit") + 1] == "1024" and "/tmp:rw,size=512m" in args


def test_limits_have_bounds():
    import pytest
    from pydantic import ValidationError
    from controller.app.projects.config import parse_project_config
    with pytest.raises(ValidationError, match="session_timeout_minutes"):
        parse_project_config(project_yaml(session_timeout_minutes=0))


def test_a_policy_stored_before_the_new_limits_keeps_the_old_values():
    from controller.app.projects.config import EffectivePolicy, effective_policy, parse_project_config
    current = effective_policy(parse_project_config(project_yaml()))
    old = current.model_dump(exclude={"session_timeout_seconds", "thinking_timeout_seconds", "setup_timeout_seconds",
                                      "max_remediations", "max_format_retries", "container_cpus", "container_memory_mb",
                                      "container_pids", "container_tmp_mb"})
    restored = EffectivePolicy.model_validate(old)
    assert (restored.session_timeout_seconds, restored.max_remediations, restored.container_tmp_mb) == (1200, 2, 256)
