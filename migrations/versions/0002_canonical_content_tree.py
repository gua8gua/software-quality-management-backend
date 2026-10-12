"""Replace copied semantic units with the canonical Docling content tree.

This migration is intentionally destructive.  Existing content and run-level
trace rows were produced from the retired semantic-unit representation and
cannot be interpreted as canonical nodes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_canonical_content_tree"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _create_trace_tables() -> None:
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
    op.create_index("ix_trace_units_run_id", "trace_units", ["run_id"])
    op.create_index("ix_trace_units_artifact_id", "trace_units", ["artifact_id"])
    op.create_index("ix_trace_units_node_id", "trace_units", ["node_id"])
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
    op.create_index("ix_trace_candidates_run_id", "trace_candidates", ["run_id"])
    op.create_index("ix_trace_candidates_source_unit_id", "trace_candidates", ["source_unit_id"])
    op.create_index("ix_trace_candidates_target_unit_id", "trace_candidates", ["target_unit_id"])
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
    op.create_index("ix_trace_links_run_id", "trace_links", ["run_id"])


def _drop_trace_tables() -> None:
    op.drop_table("trace_links")
    op.drop_table("trace_candidates")
    op.drop_table("trace_units")


def upgrade() -> None:
    _drop_trace_tables()
    # Completed runs from the retired semantic-unit representation would have
    # stale metrics after their units/candidates are dropped.
    op.execute(sa.text("DELETE FROM tlr_runs"))
    # Old artifacts cannot be valid without rebuilding their canonical tree.
    # Keep project/tenant boundaries, but require files to be re-ingested.
    op.execute(sa.text("DELETE FROM artifacts"))
    op.execute(sa.text("DELETE FROM files"))
    op.drop_table("content_nodes")
    op.create_table(
        "content_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("content_nodes.id", ondelete="CASCADE"),
        ),
        sa.Column("sibling_ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("original_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("docling_ref", sa.String(512)),
        sa.Column("docling_parent_ref", sa.String(512)),
        sa.Column("docling_children_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("docling_depth", sa.Integer()),
        sa.Column("heading_level", sa.Integer()),
        sa.Column("content_layer", sa.String(32), nullable=False, server_default="body"),
        sa.Column("hierarchy_status", sa.String(32), nullable=False, server_default="original"),
        sa.Column("hierarchy_source", sa.String(128), nullable=False, server_default="parser"),
        sa.Column("provenance_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("content_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("artifact_id", "reading_order"),
        sa.UniqueConstraint("artifact_id", "docling_ref"),
    )
    op.create_index("ix_content_nodes_artifact_id", "content_nodes", ["artifact_id"])
    op.create_index("ix_content_nodes_parent_id", "content_nodes", ["parent_id"])
    op.create_table(
        "content_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_node_id",
            sa.String(36),
            sa.ForeignKey("content_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_node_id",
            sa.String(36),
            sa.ForeignKey("content_nodes.id", ondelete="CASCADE"),
        ),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="resolved"),
        sa.Column("origin", sa.String(128), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_docling_ref", sa.String(512)),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_content_relations_artifact_id", "content_relations", ["artifact_id"])
    op.create_index("ix_content_relations_source_node_id", "content_relations", ["source_node_id"])
    op.create_index("ix_content_relations_target_node_id", "content_relations", ["target_node_id"])
    op.create_index("ix_content_relations_relation_type", "content_relations", ["relation_type"])
    _create_trace_tables()


def downgrade() -> None:
    _drop_trace_tables()
    op.execute(sa.text("DELETE FROM tlr_runs"))
    op.execute(sa.text("DELETE FROM artifacts"))
    op.execute(sa.text("DELETE FROM files"))
    op.drop_table("content_relations")
    op.drop_table("content_nodes")
    op.create_table(
        "content_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("content_nodes.id", ondelete="CASCADE"),
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("artifact_id", "ordinal"),
    )
    op.create_index("ix_content_nodes_artifact_id", "content_nodes", ["artifact_id"])
    op.create_index("ix_content_nodes_parent_id", "content_nodes", ["parent_id"])
    _create_trace_tables()
