"""Preserve artifact structure and preprocessing provenance."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_0004"
down_revision = "20260905_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tlr_artifacts", sa.Column("structure", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column(
        "tlr_elements", sa.Column("processing", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade():
    op.drop_column("tlr_elements", "processing")
    op.drop_column("tlr_artifacts", "structure")
