"""Add requirement Agent evaluation state and deterministic argument tree."""

import sqlalchemy as sa
from alembic import op

revision = "0005_agent_evaluation"
down_revision = "0004_tlr_reliability_and_rrf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requirement_evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("tlr_run_id", sa.String(36), sa.ForeignKey("tlr_runs.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_requirement_evaluation_run_status",
        ),
    )
    op.create_index("ix_requirement_evaluation_runs_tenant_id", "requirement_evaluation_runs", ["tenant_id"])
    op.create_index("ix_requirement_evaluation_runs_project_id", "requirement_evaluation_runs", ["project_id"])
    op.create_index("ix_requirement_evaluation_runs_tlr_run_id", "requirement_evaluation_runs", ["tlr_run_id"])

    op.create_table(
        "requirement_evaluation_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("srs_quote", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "requirement_node_id", "ordinal"),
        sa.CheckConstraint(
            "claim_type IN ('behavior','object_relationship','condition_constraint','result_state_effect','other')",
            name="ck_requirement_evaluation_claim_type",
        ),
    )
    op.create_index("ix_requirement_evaluation_claims_run_id", "requirement_evaluation_claims", ["run_id"])
    op.create_index("ix_requirement_evaluation_claims_requirement_node_id", "requirement_evaluation_claims", ["requirement_node_id"])

    op.create_table(
        "requirement_evaluation_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("requirement_evaluation_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=True),
        sa.Column("evidence_kind", sa.String(16), nullable=False),
        sa.Column("verification_status", sa.String(24), nullable=False),
        sa.Column("exact_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("location", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("acquisition_source", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint("evidence_kind IN ('supporting','counter','unverified')", name="ck_requirement_evaluation_evidence_kind"),
        sa.CheckConstraint("verification_status IN ('verified_trace','verified_pair','canonical_only','unverified')", name="ck_requirement_evaluation_evidence_verification"),
    )
    op.create_index("ix_requirement_evaluation_evidence_run_id", "requirement_evaluation_evidence", ["run_id"])
    op.create_index("ix_requirement_evaluation_evidence_claim_id", "requirement_evaluation_evidence", ["claim_id"])

    op.create_table(
        "requirement_claim_verdicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("requirement_evaluation_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("supporting_evidence_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("counter_evidence_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("unresolved_gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("run_id", "claim_id"),
        sa.CheckConstraint("verdict IN ('supported','contradicted','insufficient_evidence','uncertain')", name="ck_requirement_claim_verdict"),
    )
    op.create_index("ix_requirement_claim_verdicts_run_id", "requirement_claim_verdicts", ["run_id"])
    op.create_index("ix_requirement_claim_verdicts_claim_id", "requirement_claim_verdicts", ["claim_id"])

    op.create_table(
        "requirement_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("unresolved_gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("argument_root_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("run_id", "requirement_node_id"),
        sa.CheckConstraint("verdict IN ('supported','contradicted','insufficient_evidence','uncertain')", name="ck_requirement_assessment_verdict"),
    )
    op.create_index("ix_requirement_assessments_run_id", "requirement_assessments", ["run_id"])
    op.create_index("ix_requirement_assessments_requirement_node_id", "requirement_assessments", ["requirement_node_id"])

    op.create_table(
        "requirement_argument_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("requirement_argument_nodes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(36), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint("node_type IN ('requirement','claim','supporting_evidence','counter_evidence','verdict','gap')", name="ck_requirement_argument_node_type"),
    )
    op.create_index("ix_requirement_argument_nodes_run_id", "requirement_argument_nodes", ["run_id"])
    op.create_index("ix_requirement_argument_nodes_requirement_node_id", "requirement_argument_nodes", ["requirement_node_id"])
    op.create_index("ix_requirement_argument_nodes_parent_id", "requirement_argument_nodes", ["parent_id"])

    op.create_table(
        "requirement_evaluation_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("requirement_evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("agent_role", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    op.create_index("ix_requirement_evaluation_audit_events_run_id", "requirement_evaluation_audit_events", ["run_id"])
    op.create_index("ix_requirement_evaluation_audit_events_requirement_node_id", "requirement_evaluation_audit_events", ["requirement_node_id"])


def downgrade() -> None:
    for table in (
        "requirement_evaluation_audit_events",
        "requirement_argument_nodes",
        "requirement_assessments",
        "requirement_claim_verdicts",
        "requirement_evaluation_evidence",
        "requirement_evaluation_claims",
        "requirement_evaluation_runs",
    ):
        op.drop_table(table)
