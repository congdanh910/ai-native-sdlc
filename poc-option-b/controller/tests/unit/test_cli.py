import pytest

from cli import sdlc


def test_parser_knows_b1_commands():
    parser = sdlc.build_parser()
    for command in (["init"], ["up"], ["down"], ["doctor", "--json"], ["logs", "api"]):
        assert parser.parse_args(command).func


def test_logs_rejects_unknown_service():
    with pytest.raises(SystemExit):
        sdlc.build_parser().parse_args(["logs", "postgres-prod"])


def test_init_writes_env_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sdlc, "ENV_FILE", tmp_path / ".env")
    args = ["init", "--operator", "danh", "--gx10-host", "cdp@gx10", "--gx10-port", "2222"]
    assert sdlc.main(args) == 0
    content = (tmp_path / ".env").read_text()
    assert "SDLC_OPERATOR=danh" in content and "GX10_SSH_HOST=cdp@gx10" in content
    assert sdlc.main(args) == 1
    assert "already exists" in capsys.readouterr().err


def test_up_stops_early_without_config(monkeypatch, capsys):
    monkeypatch.setattr(sdlc.health, "check_config",
                        lambda s: sdlc.health.Check("config", sdlc.health.FAIL, ".env is missing", "Run ./sdlc init"))
    assert sdlc.main(["up"]) == 1
    assert "Run ./sdlc init" in capsys.readouterr().err
