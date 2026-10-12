"""Clean traceability baseline schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_table(
        "files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_files_project_id", "files", ["project_id"])
    op.create_index("ix_files_sha256", "files", ["sha256"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id")),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("revision", sa.String(128), nullable=False, server_default="1"),
        sa.Column("engineering_role", sa.String(32), nullable=False, server_default="other"),
        sa.Column("modality", sa.String(32), nullable=False, server_default="text"),
        sa.Column("file_format", sa.String(32), nullable=False, server_default=""),
        sa.Column("language", sa.String(32), nullable=False, server_default=""),
        sa.Column("participation", sa.String(32), nullable=False, server_default="included"),
        sa.Column("locator", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("inline_content", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("project_id", "external_id"),
    )
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
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
            "parent_id", sa.String(36), sa.ForeignKey("content_nodes.id", ondelete="CASCADE")
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
    op.create_table(
        "tlr_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("stage", sa.String(32), nullable=False, server_default="created"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="ck_tlr_run_status"
        ),
    )
    op.create_index("ix_tlr_runs_project_id", "tlr_runs", ["project_id"])
    op.create_index("ix_tlr_runs_tenant_id", "tlr_runs", ["tenant_id"])
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


def downgrade() -> None:
    for table in (
        "trace_links",
        "trace_candidates",
        "trace_units",
        "tlr_runs",
        "content_nodes",
        "artifacts",
        "files",
        "projects",
    ):
        op.drop_table(table)
