"""Add standard RRF diagnostics and recoverable synopsis state."""

import sqlalchemy as sa
from alembic import op

revision = "0004_tlr_reliability_and_rrf"
down_revision = "0003_trace_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("semantic_annotations") as batch:
        batch.alter_column("semantic_synopsis", existing_type=sa.Text(), nullable=True)
        batch.add_column(
            sa.Column("synopsis_status", sa.String(16), nullable=False, server_default="completed")
        )
        batch.add_column(sa.Column("synopsis_error", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_semantic_annotation_synopsis_status",
            "synopsis_status IN ('completed','failed')",
        )

    with op.batch_alter_table("trace_candidates") as batch:
        batch.add_column(sa.Column("rrf_score", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("fused_rank", sa.Integer(), nullable=True))
        batch.drop_column("pre_rerank_rank")

    op.execute(sa.text("DELETE FROM trace_links WHERE relation <> 'direct'"))
    with op.batch_alter_table("trace_links") as batch:
        batch.drop_constraint("ck_trace_link_relation", type_="check")
        batch.create_check_constraint("ck_trace_link_relation", "relation = 'direct'")


def downgrade() -> None:
    with op.batch_alter_table("trace_links") as batch:
        batch.drop_constraint("ck_trace_link_relation", type_="check")
        batch.create_check_constraint(
            "ck_trace_link_relation", "relation IN ('direct','related','uncertain')"
        )

    with op.batch_alter_table("trace_candidates") as batch:
        batch.add_column(
            sa.Column("pre_rerank_rank", sa.Integer(), nullable=False, server_default="0")
        )
        batch.drop_column("fused_rank")
        batch.drop_column("rrf_score")

    op.execute(
        sa.text(
            "UPDATE semantic_annotations SET semantic_synopsis = '' "
            "WHERE semantic_synopsis IS NULL"
        )
    )
    with op.batch_alter_table("semantic_annotations") as batch:
        batch.drop_constraint("ck_semantic_annotation_synopsis_status", type_="check")
        batch.drop_column("synopsis_error")
        batch.drop_column("synopsis_status")
        batch.alter_column("semantic_synopsis", existing_type=sa.Text(), nullable=False)
