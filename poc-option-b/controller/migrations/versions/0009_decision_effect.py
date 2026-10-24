"""A decision taken directly can state its effect on its items (confirmed, out of scope, changed).

Revision ID: 0009_decision_effect
Revises: 0008_discovery_questions
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_decision_effect"
down_revision = "0008_discovery_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("effect", sa.String(16), nullable=True))
    op.add_column("decisions", sa.Column("new_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "new_text")
    op.drop_column("decisions", "effect")
