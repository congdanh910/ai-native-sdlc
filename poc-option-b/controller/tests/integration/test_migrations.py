import pytest

from controller.app import migrations

pytestmark = pytest.mark.integration


def test_upgrade_reaches_head(database):
    assert migrations.current_revision(database) == migrations.head_revision()
