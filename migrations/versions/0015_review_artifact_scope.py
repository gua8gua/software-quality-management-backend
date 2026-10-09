"""Persist user-selected artifact scope for consistency review workspaces."""

import sqlalchemy as sa
from alembic import op

revision = "0015_review_artifact_scope"
down_revision = "0014_restore_model_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("review_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("tlr_run_id", sa.String(36), sa.ForeignKey("tlr_runs.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("results_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for field in ("project_id", "tlr_run_id"):
        op.create_index(f"ix_review_batches_{field}", "review_batches", [field])
    op.add_column("requirement_reviews", sa.Column(
        "scope_json", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("requirement_reviews") as batch:
        batch.add_column(sa.Column("batch_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_requirement_reviews_batch_id", "review_batches", ["batch_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_requirement_reviews_batch_id", ["batch_id"])


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade would lose artifact-level analysis scope")
