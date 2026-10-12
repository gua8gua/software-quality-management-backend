"""Add obligation-level evaluation, quality review, and repair previews."""

import sqlalchemy as sa
from alembic import op

revision = "0008_obligation_repair"
down_revision = "0007_derived_slices"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    for column in (
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("schema_hash", sa.String(64), nullable=True),
    ):
        op.add_column("requirement_evaluation_tasks", column)

    for column in (
        sa.Column("role_prompt_version", sa.String(64), nullable=True),
        sa.Column("role_prompt_hash", sa.String(64), nullable=True),
        sa.Column("role_schema_version", sa.String(64), nullable=True),
        sa.Column("role_schema_hash", sa.String(64), nullable=True),
        sa.Column("synopsis_prompt_version", sa.String(64), nullable=True),
        sa.Column("synopsis_prompt_hash", sa.String(64), nullable=True),
        sa.Column("synopsis_schema_version", sa.String(64), nullable=True),
        sa.Column("synopsis_schema_hash", sa.String(64), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
    ):
        op.add_column("semantic_annotations", column)
    op.create_index("ix_semantic_annotations_input_fingerprint", "semantic_annotations", ["input_fingerprint"])

    op.add_column("trace_candidates", sa.Column("prompt_hash", sa.String(64), nullable=True))
    op.add_column("trace_candidates", sa.Column("schema_hash", sa.String(64), nullable=True))

    op.create_table(
        "requirement_analysis_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("discovery_ambiguities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("semantic_ambiguities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("extraction_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("span_accounting_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("run_id", "requirement_node_id"),
    )
    _indexes("requirement_analysis_states", ("run_id", "requirement_node_id"))

    op.create_table(
        "requirement_span_accounting",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("span_id", sa.String(64), sa.ForeignKey("requirement_evaluation_spans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("requirement_evaluation_claims.id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("ambiguity", sa.Text(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="deterministic"),
        sa.UniqueConstraint("run_id", "span_id"),
        sa.CheckConstraint("status IN ('claim_anchor','claim_context','non_requirement','unresolved')", name="ck_requirement_span_accounting_status"),
    )
    _indexes("requirement_span_accounting", ("run_id", "requirement_node_id", "span_id", "claim_id"))

    op.create_table(
        "requirement_obligations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("requirement_evaluation_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("obligation_type", sa.String(40), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="valid"),
        sa.Column("ambiguities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("coverage_status", sa.String(20), nullable=False, server_default="uncovered"),
        sa.Column("satisfaction", sa.String(32), nullable=True),
        sa.Column("investigation_status", sa.String(20), nullable=False, server_default="incomplete"),
        sa.Column("search_protocol_status", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="not_checked"),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
        sa.UniqueConstraint("run_id", "claim_id", "ordinal"),
        sa.CheckConstraint("obligation_type IN ('function_behavior','object_relationship','precondition_trigger','constraint','input','output','data_requirement','process_order','state_result','performance_metric','quality_attribute','interface','required_design_component','exception','boundary_condition')", name="ck_requirement_obligation_type"),
        sa.CheckConstraint("status IN ('valid','ambiguous','unresolved')", name="ck_requirement_obligation_status"),
    )
    _indexes("requirement_obligations", ("run_id", "requirement_node_id", "claim_id", "obligation_type", "input_fingerprint"))

    op.add_column("requirement_evaluation_evidence", sa.Column("obligation_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_requirement_evidence_obligation", "requirement_evaluation_evidence", "requirement_obligations", ["obligation_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_requirement_evaluation_evidence_obligation_id", "requirement_evaluation_evidence", ["obligation_id"])

    op.create_table(
        "requirement_obligation_investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("obligation_id", sa.String(36), sa.ForeignKey("requirement_obligations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("queue_rank", sa.Integer(), nullable=False),
        sa.Column("candidate_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("retrieval_diagnostics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("current_slice_id", sa.String(64), sa.ForeignKey("requirement_evaluation_slices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("context_node_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="not_checked"),
        sa.Column("semantic_relation", sa.String(32), nullable=True),
        sa.Column("evidence_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("model_version", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("schema_hash", sa.String(64), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "obligation_id", "candidate_node_id"),
    )
    _indexes("requirement_obligation_investigations", ("run_id", "obligation_id", "candidate_node_id", "input_fingerprint"))

    op.create_table(
        "requirement_obligation_verdicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("obligation_id", sa.String(36), sa.ForeignKey("requirement_obligations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("coverage_status", sa.String(20), nullable=False),
        sa.Column("satisfaction", sa.String(32), nullable=False),
        sa.Column("investigation_completeness", sa.String(20), nullable=False),
        sa.Column("supporting_evidence_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("counter_evidence_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("unresolved_gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="succeeded"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="valid"),
        sa.UniqueConstraint("run_id", "obligation_id"),
    )
    _indexes("requirement_obligation_verdicts", ("run_id", "obligation_id"))

    op.create_table(
        "requirement_quality_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("characteristic", sa.String(24), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("defect_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="succeeded"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="valid"),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "requirement_node_id", "characteristic"),
    )
    _indexes("requirement_quality_assessments", ("run_id", "requirement_node_id", "characteristic", "input_fingerprint"))

    op.create_table(
        "requirement_improvement_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("quality_assessment_id", sa.String(36), sa.ForeignKey("requirement_quality_assessments.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("suggested_text", sa.Text(), nullable=True),
        sa.Column("semantic_preservation_notes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("clarification_request", sa.Text(), nullable=True),
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="succeeded"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="valid"),
    )

    op.create_table(
        "requirement_repair_recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("obligation_id", sa.String(36), sa.ForeignKey("requirement_obligations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("issue_class", sa.String(40), nullable=False),
        sa.Column("target", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("plan_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("suggested_text", sa.Text(), nullable=True),
        sa.Column("requires_human_decision", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
    )
    _indexes("requirement_repair_recommendations", ("run_id", "requirement_node_id", "obligation_id", "issue_class", "input_fingerprint"))

    op.create_table(
        "requirement_repair_impacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recommendation_id", sa.String(36), sa.ForeignKey("requirement_repair_recommendations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("affected_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.UniqueConstraint("recommendation_id", "affected_node_id", "relation"),
    )
    _indexes("requirement_repair_impacts", ("recommendation_id", "affected_node_id"))

    op.create_table(
        "requirement_repair_previews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recommendation_id", sa.String(36), sa.ForeignKey("requirement_repair_recommendations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("target_document", sa.String(16), nullable=False),
        sa.Column("target_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("proposed_text", sa.Text(), nullable=True),
        sa.Column("intended_change_preserved", sa.Boolean(), nullable=True),
        sa.Column("structural_validity", sa.String(20), nullable=False, server_default="not_checked"),
        sa.Column("semantic_drift", sa.String(20), nullable=False, server_default="not_checked"),
        sa.Column("consistency_status", sa.String(20), nullable=False, server_default="not_checked"),
        sa.Column("revalidation_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
    )


def downgrade() -> None:
    op.drop_table("requirement_repair_previews")
    op.drop_table("requirement_repair_impacts")
    op.drop_table("requirement_repair_recommendations")
    op.drop_table("requirement_improvement_proposals")
    op.drop_table("requirement_quality_assessments")
    op.drop_table("requirement_obligation_verdicts")
    op.drop_table("requirement_obligation_investigations")
    op.drop_index("ix_requirement_evaluation_evidence_obligation_id", table_name="requirement_evaluation_evidence")
    op.drop_constraint("fk_requirement_evidence_obligation", "requirement_evaluation_evidence", type_="foreignkey")
    op.drop_column("requirement_evaluation_evidence", "obligation_id")
    op.drop_table("requirement_obligations")
    op.drop_table("requirement_span_accounting")
    op.drop_table("requirement_analysis_states")
    op.drop_column("trace_candidates", "schema_hash")
    op.drop_column("trace_candidates", "prompt_hash")
    op.drop_index("ix_semantic_annotations_input_fingerprint", table_name="semantic_annotations")
    for column in (
        "input_fingerprint", "synopsis_schema_hash", "synopsis_schema_version",
        "synopsis_prompt_hash", "synopsis_prompt_version", "role_schema_hash",
        "role_schema_version", "role_prompt_hash", "role_prompt_version",
    ):
        op.drop_column("semantic_annotations", column)
    op.drop_column("requirement_evaluation_tasks", "schema_hash")
    op.drop_column("requirement_evaluation_tasks", "prompt_hash")

