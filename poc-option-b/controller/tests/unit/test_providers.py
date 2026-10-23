"""When a provider's subscription window is back."""

from datetime import datetime, timezone

from controller.app.providers import provider_of, reset_time


def test_the_reset_time_a_provider_gives_is_read_in_local_time():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    at = reset_time("You've hit your usage limit ... try again at 7:44 PM.", now).astimezone()
    assert (at.hour, at.minute) == (19, 44) and at > now
    assert reset_time("limit reached", now) > now  # no time given: subscription_wait_seconds later
    assert provider_of("codex-readonly") == "codex" and provider_of("claude") == "claude"
