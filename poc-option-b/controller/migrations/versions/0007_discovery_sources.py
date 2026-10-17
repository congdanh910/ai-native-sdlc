"""Discovery sources: raw requirement files of a discovery run.

Revision ID: 0007_discovery_sources
Revises: 0006_gate_checklist
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_discovery_sources"
down_revision = "0006_gate_checklist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("text_path", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("removed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sha256", name="uq_sources_run_sha"),
    )


def downgrade() -> None:
    op.drop_table("sources")
