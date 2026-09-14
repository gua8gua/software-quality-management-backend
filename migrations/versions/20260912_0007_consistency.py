"""Add independent consistency runs and review evidence.

Revision ID: 20260912_0007
Revises: 20260906_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0007"
down_revision = "20260906_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "consistency_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("tlr_run_id", sa.String(36), sa.ForeignKey("tlr_runs.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name in ("tenant_id", "project_id", "tlr_run_id"):
        op.create_index(f"ix_consistency_runs_{name}", "consistency_runs", [name])
    op.create_table(
        "consistency_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("consistency_runs.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_consistency_reviews_run_id", "consistency_reviews", ["run_id"])


def downgrade():
    op.drop_table("consistency_reviews")
    op.drop_table("consistency_runs")
