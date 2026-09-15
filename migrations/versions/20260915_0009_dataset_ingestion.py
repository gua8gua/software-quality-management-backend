import sqlalchemy as sa
from alembic import op

revision = "20260915_0009"
down_revision = "20260913_0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tlr_artifacts", sa.Column("content_mode", sa.String(16), nullable=False, server_default="inline"))
    op.add_column("tlr_artifacts", sa.Column("content_path", sa.Text(), nullable=True))
    with op.batch_alter_table("tlr_artifacts") as batch:
        batch.alter_column("content", existing_type=sa.Text(), nullable=True)
    op.add_column("tlr_datasets", sa.Column("source_dataset", sa.String(128), nullable=False, server_default=""))
    op.create_table(
        "tlr_dataset_trace_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("tlr_datasets.id"), nullable=False),
        sa.Column("source_artifact_id", sa.String(36), sa.ForeignKey("tlr_artifacts.id"), nullable=False),
        sa.Column("target_artifact_id", sa.String(36), sa.ForeignKey("tlr_artifacts.id"), nullable=False),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("source_dataset", sa.String(128), nullable=False),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("original_relation", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.UniqueConstraint("dataset_id", "source_artifact_id", "target_artifact_id", "relation"),
    )
    op.create_index("ix_tlr_dataset_trace_links_dataset_id", "tlr_dataset_trace_links", ["dataset_id"])


def downgrade():
    op.drop_table("tlr_dataset_trace_links")
    op.drop_column("tlr_datasets", "source_dataset")
    op.drop_column("tlr_artifacts", "content_path")
    op.drop_column("tlr_artifacts", "content_mode")
