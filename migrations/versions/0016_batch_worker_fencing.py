"""Fence long-running review workers across web processes."""

import sqlalchemy as sa
from alembic import op

revision = "0016_batch_worker_fencing"
down_revision = "0015_review_artifact_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_batches", sa.Column("worker_token", sa.String(64), nullable=True))
    op.add_column("review_batches", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_review_batches_worker_token", "review_batches", ["worker_token"])


def downgrade() -> None:
    op.drop_index("ix_review_batches_worker_token", table_name="review_batches")
    op.drop_column("review_batches", "lease_expires_at")
    op.drop_column("review_batches", "worker_token")
