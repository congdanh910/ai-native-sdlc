"""Eval runs and experiment run settings.

Revision ID: 0004_evals
Revises: 0003_reasoning
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_evals"
down_revision = "0003_reasoning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("suite", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("baseline_id", sa.UUID(), sa.ForeignKey("eval_runs.id"), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("runs", sa.Column("stage_overrides", postgresql.JSONB(astext_type=sa.Text()),
                                    server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("runs", sa.Column("eval_run_id", sa.UUID(), sa.ForeignKey("eval_runs.id"), nullable=True))
    op.add_column("runs", sa.Column("eval_case", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "eval_case")
    op.drop_column("runs", "eval_run_id")
    op.drop_column("runs", "stage_overrides")
    op.drop_table("eval_runs")
