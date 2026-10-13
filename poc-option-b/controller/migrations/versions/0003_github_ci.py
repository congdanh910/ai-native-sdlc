"""Persist draft PR identity and signed webhook replay protection.

Revision ID: 0003_github_ci
Revises: 0002_run_executor
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_github_ci"
down_revision = "0002_run_executor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("repo_full_name", sa.String(255), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("ci_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("repo_full_name", "number", name="uq_pull_requests_repo_number"),
    )
    op.create_index("ix_pull_requests_repo_head", "pull_requests", ["repo_full_name", "head_sha"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.String(100), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_pull_requests_repo_head", table_name="pull_requests")
    op.drop_table("pull_requests")
