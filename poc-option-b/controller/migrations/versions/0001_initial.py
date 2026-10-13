"""Create the initial authoritative controller schema.

Revision ID: 0001_initial
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("repo_id", sa.String(255), nullable=False),
        sa.Column("base_commit", sa.String(64), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("failure_category", sa.String(40)),
        sa.Column("failure_stage", sa.String(40)),
        sa.Column("failure_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_runs_status", "runs", ["status"])

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", json_type(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "version", name="uq_plans_run_version"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_task_id", sa.String(255), nullable=False),
        sa.Column("payload_jsonb", json_type(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("executor", sa.String(20)),
        sa.UniqueConstraint("plan_id", "external_task_id", name="uq_tasks_plan_external_id"),
    )
    op.create_index("ix_tasks_run_status", "tasks", ["run_id", "status"])

    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("depends_on_task_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), sa.ForeignKey("plans.id", ondelete="RESTRICT")),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("expected_run_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_approvals_run_stage", "approvals", ["run_id", "stage"])

    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("task_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("process_id", sa.Integer()),
        sa.Column("process_group_id", sa.Integer()),
        sa.Column("worktree_path", sa.Text()),
        sa.Column("branch", sa.String(255)),
        sa.Column("result_jsonb", json_type()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_executions_status_lease", "executions", ["status", "lease_until"])

    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload_jsonb", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_run_created", "events", ["run_id", "created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_index("ix_events_run_created", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_executions_status_lease", table_name="executions")
    op.drop_table("executions")
    op.drop_index("ix_approvals_run_stage", table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("task_dependencies")
    op.drop_index("ix_tasks_run_status", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("plans")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_table("runs")
