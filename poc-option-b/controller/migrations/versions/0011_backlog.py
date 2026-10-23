"""The story backlog of a project: order, dependencies and the Tech Lead's approval.

Revision ID: 0011_backlog
Revises: 0010_approval_stage_length
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_backlog"
down_revision = "0010_approval_stage_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backlogs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False, unique=True),
        sa.Column("items", JSONB, nullable=False),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("srs_commit", sa.String(40)),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backlogs")
