"""Baseline: an empty schema under migration control.

The run, task and artifact tables are added in B2.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-13
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
