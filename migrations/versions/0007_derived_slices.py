"""Persist token-budget derived slices without changing canonical nodes."""

import sqlalchemy as sa
from alembic import op

revision = "0007_derived_slices"
down_revision = "0006_execution_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requirement_evaluation_slices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "node_id", "purpose", "ordinal", "input_fingerprint"),
    )
    for column in ("run_id", "node_id", "input_fingerprint"):
        op.create_index(
            f"ix_requirement_evaluation_slices_{column}",
            "requirement_evaluation_slices",
            [column],
        )


def downgrade() -> None:
    op.drop_table("requirement_evaluation_slices")
