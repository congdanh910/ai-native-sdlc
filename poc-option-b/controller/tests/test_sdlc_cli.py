from types import SimpleNamespace

import pytest

from scripts import sdlc


def test_cli_rejects_non_loopback_controller_before_sending_token(monkeypatch) -> None:
    monkeypatch.setenv("SDLC_CONTROLLER_URL", "https://example.com")
    monkeypatch.setattr(sdlc, "get_settings", lambda: SimpleNamespace(controller_token="test-token"))
    called = False

    def unexpected_network_request(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network request must not be made")

    monkeypatch.setattr(sdlc, "urlopen", unexpected_network_request)
    with pytest.raises(SystemExit, match="local loopback"):
        sdlc.request("GET", "/api/runs")
    assert called is False
