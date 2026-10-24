"""Approval stages longer than eight letters, such as the foundation run's onboarding gate.

Revision ID: 0010_approval_stage_length
Revises: 0009_decision_effect
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_approval_stage_length"
down_revision = "0009_decision_effect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("approvals", "stage", type_=sa.String(32), existing_type=sa.String(8), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("approvals", "stage", type_=sa.String(8), existing_type=sa.String(32), existing_nullable=False)
