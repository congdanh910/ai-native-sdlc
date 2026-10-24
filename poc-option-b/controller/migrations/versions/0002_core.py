"""core tables

Revision ID: 0002_core
Revises: 0001_baseline
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002_core'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('projects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('repo_url', sa.Text(), nullable=False),
    sa.Column('default_branch', sa.String(length=255), nullable=False),
    sa.Column('mirror_path', sa.Text(), nullable=False),
    sa.Column('readiness', sa.String(length=16), nullable=False),
    sa.Column('readiness_report', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('checked_commit', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('base_commit', sa.String(length=40), nullable=True),
    sa.Column('branch', sa.String(length=255), nullable=True),
    sa.Column('worktree_path', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('stage_modes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('commit_artifacts', sa.Boolean(), nullable=False),
    sa.Column('awaiting_start', sa.String(length=16), nullable=True),
    sa.Column('resume_status', sa.String(length=32), nullable=True),
    sa.Column('status_reason', sa.Text(), nullable=True),
    sa.Column('experiment', sa.Boolean(), nullable=False),
    sa.Column('human_corrected', sa.Boolean(), nullable=False),
    sa.Column('failure_category', sa.String(length=32), nullable=True),
    sa.Column('failure_stage', sa.String(length=32), nullable=True),
    sa.Column('failure_role', sa.String(length=32), nullable=True),
    sa.Column('failure_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_runs_project', 'runs', ['project_id'], unique=False)
    op.create_index('ix_runs_status', 'runs', ['status'], unique=False)
    op.create_table('artifacts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('clarifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('blocking', sa.Boolean(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('author_kind', sa.String(length=8), nullable=False),
    sa.Column('author', sa.String(length=64), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('repo_path', sa.Text(), nullable=True),
    sa.Column('commit_sha', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'kind', 'version')
    )
    op.create_table('events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('acting_role', sa.String(length=32), nullable=True),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_events_run', 'events', ['run_id'], unique=False)
    op.create_table('plans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('author_kind', sa.String(length=8), nullable=False),
    sa.Column('author', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('test_plan', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('grounding_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('commit_sha', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'version')
    )
    op.create_table('pull_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('head_sha', sa.String(length=40), nullable=True),
    sa.Column('ci_status', sa.String(length=16), nullable=False),
    sa.Column('review_status', sa.String(length=24), nullable=False),
    sa.Column('merged_sha', sa.String(length=40), nullable=True),
    sa.Column('simulated', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id')
    )
    op.create_table('approvals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('stage', sa.String(length=8), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=True),
    sa.Column('plan_id', sa.UUID(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('acting_role', sa.String(length=32), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('external_task_id', sa.String(length=32), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'plan_id', 'external_task_id')
    )
    op.create_table('executions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('side_effects', sa.Boolean(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('worker_id', sa.String(length=64), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('engine', sa.String(length=32), nullable=True),
    sa.Column('prompt_version', sa.String(length=64), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_executions_status', 'executions', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_executions_status', table_name='executions')
    op.drop_table('executions')
    op.drop_table('tasks')
    op.drop_table('approvals')
    op.drop_table('pull_requests')
    op.drop_table('plans')
    op.drop_index('ix_events_run', table_name='events')
    op.drop_table('events')
    op.drop_table('documents')
    op.drop_table('clarifications')
    op.drop_table('artifacts')
    op.drop_index('ix_runs_status', table_name='runs')
    op.drop_index('ix_runs_project', table_name='runs')
    op.drop_table('runs')
    op.drop_table('projects')
