"""Pin a deterministic coding executor on each run.

Revision ID: 0002_run_executor
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_run_executor"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs", sa.Column("executor", sa.String(length=20), nullable=False, server_default="codex")
    )


def downgrade() -> None:
    op.drop_column("runs", "executor")
