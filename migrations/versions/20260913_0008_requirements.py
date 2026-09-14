"""Independent grounded requirement hierarchy snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0008"
down_revision = "20260912_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "requirement_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("tlr_datasets.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for key in ("tenant_id", "project_id", "dataset_id"):
        op.create_index("ix_requirement_analyses_" + key, "requirement_analyses", [key])


def downgrade():
    op.drop_table("requirement_analyses")
