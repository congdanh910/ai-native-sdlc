"""The review checklist recorded with each gate decision.

Revision ID: 0006_gate_checklist
Revises: 0005_run_kind
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_gate_checklist"
down_revision = "0005_run_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()),
                                         server_default=sa.text("'{}'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column("approvals", "checklist")
