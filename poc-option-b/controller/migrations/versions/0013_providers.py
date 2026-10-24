"""AI providers: each role's engines in order (the Platform Admin's settings) and the engines out of quota.

Revision ID: 0013_providers
Revises: 0012_backlog_auto_start
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_providers"
down_revision = "0012_backlog_auto_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_settings",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "engine_limits",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("limited_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("engine_limits")
    op.drop_table("provider_settings")
