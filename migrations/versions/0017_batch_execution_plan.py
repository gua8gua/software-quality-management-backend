"""Bind each queue attempt to an explicit continuation/retry scope."""
import sqlalchemy as sa
from alembic import op

revision = "0017_batch_execution_plan"
down_revision = "0016_batch_worker_fencing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_batches", sa.Column("execution_plan_json", sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    op.drop_column("review_batches", "execution_plan_json")
