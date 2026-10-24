"""Plan regeneration link and delayed jobs.

Revision ID: 0003_reasoning
Revises: 0002_core
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_reasoning"
down_revision = "0002_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("regenerated_from", sa.UUID(), sa.ForeignKey("plans.id"), nullable=True))
    op.add_column("executions", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "available_at")
    op.drop_column("plans", "regenerated_from")
