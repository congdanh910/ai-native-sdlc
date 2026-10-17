import pytest

from controller.app import tunnel


def test_tunnel_argv_forwards_loopback_only(settings):
    argv = tunnel.tunnel_argv(settings)
    assert argv[:2] == ["ssh", "-N"]
    assert "127.0.0.1:11435:127.0.0.1:11437" in argv
    assert "BatchMode=yes" in argv and "ExitOnForwardFailure=yes" in argv
    assert argv[-2:] == ["--", "cdp@gx10.example"]


def test_status_prefers_owned_process(settings, monkeypatch):
    monkeypatch.setattr(tunnel.TUNNEL.__class__, "pid", lambda self: 4242)
    assert tunnel.tunnel_status(settings) == tunnel.TunnelStatus("owned", 4242)


def test_status_detects_external_tunnel(settings, monkeypatch):
    monkeypatch.setattr(tunnel.TUNNEL.__class__, "pid", lambda self: None)
    monkeypatch.setattr(tunnel, "port_listening", lambda port: True)
    assert tunnel.tunnel_status(settings).state == "external"


def test_ensure_does_not_start_when_port_is_served(settings, monkeypatch):
    monkeypatch.setattr(tunnel.TUNNEL.__class__, "pid", lambda self: None)
    monkeypatch.setattr(tunnel, "port_listening", lambda port: True)
    monkeypatch.setattr(tunnel.TUNNEL.__class__, "start", lambda *a, **k: pytest.fail("must not start"))
    assert tunnel.ensure_tunnel(settings).state == "external"


def test_ensure_rejects_bad_configuration(settings, monkeypatch):
    monkeypatch.setattr(tunnel.TUNNEL.__class__, "pid", lambda self: None)
    monkeypatch.setattr(tunnel, "port_listening", lambda port: False)
    bad = settings.model_copy(update={"gx10_ssh_host": "not-a-destination"})
    with pytest.raises(RuntimeError, match="GX10_SSH_HOST"):
        tunnel.ensure_tunnel(bad)
