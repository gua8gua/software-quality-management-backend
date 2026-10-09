"""Remove the second-retrieval evaluation schema and add independent review state.

This migration intentionally discards legacy evaluation/repair rows. TLR rows and
canonical content remain untouched. Apply only after archiving legacy results if needed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_review_tools"
down_revision = "0008_obligation_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        "role_prompt_version", "role_prompt_hash", "role_schema_version", "role_schema_hash",
        "synopsis_prompt_version", "synopsis_prompt_hash", "synopsis_schema_version",
        "synopsis_schema_hash",
    ):
        op.drop_column("semantic_annotations", column)
    for table in (
        "requirement_repair_previews", "requirement_repair_impacts",
        "requirement_repair_recommendations", "requirement_improvement_proposals",
        "requirement_quality_assessments", "requirement_obligation_verdicts",
        "requirement_obligation_investigations", "requirement_evaluation_evidence",
        "requirement_span_accounting", "requirement_obligations",
        "requirement_claim_investigations", "requirement_claim_verdicts",
        "requirement_assessments", "requirement_argument_nodes",
        "requirement_evaluation_tasks", "requirement_evaluation_slices",
        "requirement_evaluation_spans", "requirement_analysis_states",
        "requirement_evaluation_audit_events", "requirement_evaluation_claims",
        "requirement_evaluation_runs",
    ):
        op.drop_table(table)

    op.create_table(
        "requirement_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("tlr_run_id", sa.String(36), sa.ForeignKey("tlr_runs.id"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "review_sentences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("requirement_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.UniqueConstraint("review_id", "ordinal"),
    )
    op.create_table(
        "review_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sentence_id", sa.String(36), sa.ForeignKey("review_sentences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "review_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("check_id", sa.String(36), sa.ForeignKey("review_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("exact_quote", sa.Text(), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
    )
    op.create_table(
        "review_mandatory_visits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("requirement_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("review_id", "design_node_id"),
    )
    for table, columns in {
        "requirement_reviews": ("project_id", "tlr_run_id", "requirement_node_id"),
        "review_sentences": ("review_id",),
        "review_checks": ("sentence_id",),
        "review_evidence": ("check_id", "design_node_id"),
        "review_mandatory_visits": ("review_id", "design_node_id"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    raise RuntimeError("Destructive evaluation removal cannot be downgraded without archived data")
