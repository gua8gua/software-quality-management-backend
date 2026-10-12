"""Add the derived semantic, hybrid retrieval and pair-level trace pipeline."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "0003_trace_pipeline"
down_revision = "0002_canonical_content_tree"
branch_labels = None
depends_on = None


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.drop_table("trace_links")
    op.drop_table("trace_candidates")
    op.drop_table("trace_units")
    op.execute(sa.text("DELETE FROM tlr_runs"))

    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        embedding_type = VECTOR()
    else:
        embedding_type = sa.JSON()

    op.create_table(
        "semantic_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("document_type", sa.String(8), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("semantic_synopsis", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("model_version", sa.String(256), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("generation_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("run_id", "node_id"),
        sa.CheckConstraint(
            "role IN ('primary','context','exclude','uncertain')",
            name="ck_semantic_annotation_role",
        ),
    )
    _indexes("semantic_annotations", "run_id", "node_id", "role")
    op.create_table(
        "retrieval_projections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column(
            "annotation_id",
            sa.String(36),
            sa.ForeignKey("semantic_annotations.id", ondelete="CASCADE"),
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("document_type", sa.String(8), nullable=False),
        sa.Column("heading_path", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type),
        sa.Column("embedding_provider", sa.String(64)),
        sa.Column("embedding_model", sa.String(256)),
        sa.Column("embedding_model_version", sa.String(256)),
        sa.UniqueConstraint("run_id", "node_id", "channel"),
        sa.CheckConstraint("channel IN ('raw','synopsis')", name="ck_projection_channel"),
    )
    _indexes("retrieval_projections", "run_id", "node_id")
    op.create_table(
        "trace_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column(
            "annotation_id", sa.String(36), sa.ForeignKey("semantic_annotations.id"), nullable=False
        ),
        sa.Column(
            "raw_projection_id",
            sa.String(36),
            sa.ForeignKey("retrieval_projections.id"),
            nullable=False,
        ),
        sa.Column(
            "synopsis_projection_id", sa.String(36), sa.ForeignKey("retrieval_projections.id")
        ),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("semantic_role", sa.String(16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "node_id", "side"),
    )
    _indexes("trace_units", "run_id", "artifact_id", "node_id")
    op.create_table(
        "trace_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_unit_id", sa.String(36), sa.ForeignKey("trace_units.id"), nullable=False),
        sa.Column("target_unit_id", sa.String(36), sa.ForeignKey("trace_units.id"), nullable=False),
        sa.Column("dense_rank", sa.Integer()),
        sa.Column("dense_score", sa.Float()),
        sa.Column("synopsis_dense_rank", sa.Integer()),
        sa.Column("synopsis_dense_score", sa.Float()),
        sa.Column("bm25_rank", sa.Integer()),
        sa.Column("bm25_score", sa.Float()),
        sa.Column("retrieval_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("structural_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("pre_rerank_rank", sa.Integer(), nullable=False),
        sa.Column("reranker_rank", sa.Integer()),
        sa.Column("reranker_score", sa.Float()),
        sa.Column("final_relation", sa.String(16)),
        sa.Column("evidence_source_node_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence", sa.JSON()),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("diagnostics", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("run_id", "source_unit_id", "target_unit_id"),
    )
    _indexes("trace_candidates", "run_id", "source_unit_id", "target_unit_id")
    op.create_table(
        "trace_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False
        ),
        sa.Column(
            "target_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False
        ),
        sa.Column(
            "source_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False
        ),
        sa.Column(
            "target_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False
        ),
        sa.Column(
            "candidate_id", sa.String(36), sa.ForeignKey("trace_candidates.id"), nullable=False
        ),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("evidence_source_node_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("validator_provider", sa.String(64), nullable=False),
        sa.Column("validator_model", sa.String(256), nullable=False),
        sa.Column("validator_model_version", sa.String(256), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "source_node_id", "target_node_id"),
        sa.CheckConstraint(
            "relation IN ('direct','related','uncertain')", name="ck_trace_link_relation"
        ),
    )
    _indexes("trace_links", "run_id", "source_node_id", "target_node_id")


def downgrade() -> None:
    for table in (
        "trace_links",
        "trace_candidates",
        "trace_units",
        "retrieval_projections",
        "semantic_annotations",
    ):
        op.drop_table(table)
    op.execute(sa.text("DELETE FROM tlr_runs"))
    op.create_table(
        "trace_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.JSON()),
        sa.UniqueConstraint("run_id", "artifact_id", "node_id", "role"),
    )
    _indexes("trace_units", "run_id", "artifact_id", "node_id")
    op.create_table(
        "trace_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_unit_id", sa.String(36), sa.ForeignKey("trace_units.id"), nullable=False),
        sa.Column("target_unit_id", sa.String(36), sa.ForeignKey("trace_units.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("score_type", sa.String(32), nullable=False, server_default="none"),
        sa.Column("retriever", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("evidence", sa.JSON()),
        sa.UniqueConstraint("run_id", "source_unit_id", "target_unit_id"),
        sa.CheckConstraint(
            "decision IN ('pending','related','unrelated')", name="ck_candidate_decision"
        ),
    )
    _indexes("trace_candidates", "run_id", "source_unit_id", "target_unit_id")
    op.create_table(
        "trace_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("tlr_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False
        ),
        sa.Column(
            "target_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False
        ),
        sa.Column("relation", sa.String(32), nullable=False, server_default="related_to"),
        sa.Column("evidence_candidate_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("run_id", "source_artifact_id", "target_artifact_id"),
    )
    _indexes("trace_links", "run_id")
