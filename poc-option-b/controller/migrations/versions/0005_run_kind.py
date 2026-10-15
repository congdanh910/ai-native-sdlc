"""Run kinds: a feature run, or a project document run (SRS).

Revision ID: 0005_run_kind
Revises: 0004_evals
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_run_kind"
down_revision = "0004_evals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("kind", sa.String(length=16), server_default="feature", nullable=False))


def downgrade() -> None:
    op.drop_column("runs", "kind")
