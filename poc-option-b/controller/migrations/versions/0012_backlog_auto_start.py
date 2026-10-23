"""The Tech Lead's switch for opening stories on their own, over delivery.auto_start of project.yaml.

Revision ID: 0012_backlog_auto_start
Revises: 0011_backlog
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_backlog_auto_start"
down_revision = "0011_backlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backlogs", sa.Column("auto_start", sa.Boolean))


def downgrade() -> None:
    op.drop_column("backlogs", "auto_start")
