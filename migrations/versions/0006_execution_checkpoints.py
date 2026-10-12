"""Separate execution failures from semantic outcomes and add resumable checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0006_execution_checkpoints"
down_revision = "0005_agent_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tlr_runs",
        sa.Column("result_status", sa.String(20), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "requirement_evaluation_runs",
        sa.Column("result_status", sa.String(20), nullable=False, server_default="legacy"),
    )

    candidate_columns = (
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("validator_provider", sa.String(64), nullable=True),
        sa.Column("validator_model", sa.String(256), nullable=True),
        sa.Column("validator_model_version", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
    )
    for column in candidate_columns:
        op.add_column("trace_candidates", column)
    op.create_index(
        "ix_trace_candidates_input_fingerprint",
        "trace_candidates",
        ["input_fingerprint"],
    )
    # Classify historical Fine rows deterministically.  Their semantic columns
    # are retained verbatim and no model is called by this migration.
    connection = op.get_bind()
    historical = connection.execute(
        sa.text("SELECT id, final_relation, diagnostics FROM trace_candidates")
    ).mappings()
    for row in historical:
        diagnostics = row["diagnostics"] or {}
        codes = set(diagnostics.get("validation") or [])
        if row["final_relation"] is None:
            values = ("pending", "not_checked", None, None, 0)
        elif "llm_invalid_structured_output" in codes:
            values = (
                "failed",
                "invalid",
                "structured_output",
                "llm_invalid_structured_output",
                2,
            )
        else:
            evidence_codes = sorted(
                codes
                & {
                    "evidence_quote_validation_failed",
                    "evidence_node_outside_package",
                    "unparsed_picture_used_as_text_evidence",
                    "direct_target_evidence_missing",
                    "direct_evidence_found_in_support_only",
                }
            )
            values = (
                ("failed", "invalid", "evidence_validation", evidence_codes[0], 2)
                if evidence_codes
                else ("succeeded", "valid", None, None, 1)
            )
        connection.execute(
            sa.text(
                "UPDATE trace_candidates SET execution_status=:execution_status, "
                "validation_status=:validation_status, error_category=:error_category, "
                "error_code=:error_code, attempt_count=:attempt_count WHERE id=:id"
            ),
            {
                "id": row["id"],
                "execution_status": values[0],
                "validation_status": values[1],
                "error_category": values[2],
                "error_code": values[3],
                "attempt_count": values[4],
            },
        )

    claim_columns = (
        sa.Column("source_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("semantic_dimensions", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("classification_labels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
    )
    for column in claim_columns:
        op.add_column("requirement_evaluation_claims", column)
    op.create_index(
        "ix_requirement_evaluation_claims_input_fingerprint",
        "requirement_evaluation_claims",
        ["input_fingerprint"],
    )
    op.add_column(
        "requirement_evaluation_evidence",
        sa.Column("span_ids", sa.JSON(), nullable=False, server_default="[]"),
    )

    verdict_columns = (
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
    )
    for column in verdict_columns:
        op.add_column("requirement_claim_verdicts", column)
    op.create_index(
        "ix_requirement_claim_verdicts_input_fingerprint",
        "requirement_claim_verdicts",
        ["input_fingerprint"],
    )

    op.add_column(
        "requirement_assessments",
        sa.Column("result_status", sa.String(20), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "requirement_assessments",
        sa.Column("coverage", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.alter_column("requirement_assessments", "verdict", existing_type=sa.String(32), nullable=True)

    op.create_table(
        "requirement_evaluation_spans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("unit_type", sa.String(32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "node_id", "purpose", "ordinal"),
        sa.CheckConstraint(
            "purpose IN ('source','evidence')",
            name="ck_requirement_evaluation_span_purpose",
        ),
    )
    for column in ("run_id", "node_id", "input_fingerprint"):
        op.create_index(
            f"ix_requirement_evaluation_spans_{column}",
            "requirement_evaluation_spans",
            [column],
        )

    op.create_table(
        "requirement_evaluation_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=True),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("requirement_evaluation_claims.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("unit_key", sa.String(256), nullable=False),
        sa.Column("execution_status", sa.String(20), nullable=False),
        sa.Column("validation_status", sa.String(20), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("model_version", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "task_type", "unit_key", "input_fingerprint"),
    )
    for column in ("run_id", "requirement_node_id", "claim_id", "task_type", "input_fingerprint"):
        op.create_index(
            f"ix_requirement_evaluation_tasks_{column}",
            "requirement_evaluation_tasks",
            [column],
        )

    op.create_table(
        "requirement_claim_investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("requirement_evaluation_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("queue_rank", sa.Integer(), nullable=False),
        sa.Column("candidate_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("retrieval_diagnostics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("execution_status", sa.String(20), nullable=False),
        sa.Column("validation_status", sa.String(20), nullable=False),
        sa.Column("semantic_relation", sa.String(32), nullable=True),
        sa.Column("evidence_span_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("model_version", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "claim_id", "candidate_node_id"),
    )
    for column in ("run_id", "claim_id", "candidate_node_id", "input_fingerprint"):
        op.create_index(
            f"ix_requirement_claim_investigations_{column}",
            "requirement_claim_investigations",
            [column],
        )


def downgrade() -> None:
    op.drop_table("requirement_claim_investigations")
    op.drop_table("requirement_evaluation_tasks")
    op.drop_table("requirement_evaluation_spans")

    op.alter_column("requirement_assessments", "verdict", existing_type=sa.String(32), nullable=False)

    op.drop_column("requirement_assessments", "coverage")
    op.drop_column("requirement_assessments", "result_status")
    for column in (
        "input_fingerprint",
        "last_error",
        "attempt_count",
        "error_code",
        "error_category",
        "validation_status",
        "execution_status",
    ):
        op.drop_column("requirement_claim_verdicts", column)
    op.drop_column("requirement_evaluation_evidence", "span_ids")
    for column in (
        "input_fingerprint",
        "last_error",
        "attempt_count",
        "error_code",
        "error_category",
        "validation_status",
        "execution_status",
        "classification_labels",
        "semantic_dimensions",
        "source_span_ids",
    ):
        op.drop_column("requirement_evaluation_claims", column)
    for column in (
        "input_fingerprint",
        "schema_version",
        "prompt_version",
        "validator_model_version",
        "validator_model",
        "validator_provider",
        "last_error",
        "attempt_count",
        "error_code",
        "error_category",
        "validation_status",
        "execution_status",
    ):
        op.drop_column("trace_candidates", column)
    op.drop_column("requirement_evaluation_runs", "result_status")
    op.drop_column("tlr_runs", "result_status")
