"""Discovery questions and decisions: roles, status and references on clarifications; a decisions table; minutes.

Revision ID: 0008_discovery_questions
Revises: 0007_discovery_sources
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_discovery_questions"
down_revision = "0007_discovery_sources"
branch_labels = None
depends_on = None

COLUMNS = (("kind", sa.String(32)), ("owner_role", sa.String(32)), ("status", sa.String(16)), ("why", sa.Text()),
           ("suggestion", sa.Text()), ("items", JSONB()), ("citations", JSONB()), ("round", sa.Integer()), ("position", sa.Integer()),
           ("answered_by", sa.String(64)), ("status_reason", sa.Text()))


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("clarifications", sa.Column(name, kind, nullable=True))
    op.add_column("sources", sa.Column("purpose", sa.String(16), server_default="requirements", nullable=False))
    op.create_table(
        "decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("question_id", sa.UUID(), sa.ForeignKey("clarifications.id"), nullable=True),
        sa.Column("source_id", sa.UUID(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("items", JSONB(), server_default="[]", nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "number", name="uq_decisions_run_number"),
    )


def downgrade() -> None:
    op.drop_table("decisions")
    op.drop_column("sources", "purpose")
    for name, _ in reversed(COLUMNS):
        op.drop_column("clarifications", name)
