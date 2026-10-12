"""Persist the TLR-bound consistency-audit workspace and observable checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0010_audit_workspace"
down_revision = "0009_review_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("requirement_reviews", sa.Column("snapshot_json", sa.JSON(), nullable=False,
                                                     server_default=sa.text("'{}'")))
    op.add_column("requirement_reviews", sa.Column("plan_json", sa.JSON(), nullable=False,
                                                     server_default=sa.text("'{}'")))
    op.add_column("requirement_reviews", sa.Column("summary_json", sa.JSON(), nullable=False,
                                                     server_default=sa.text("'{}'")))
    op.add_column("requirement_reviews", sa.Column("tlr_result_fingerprint", sa.String(64), nullable=True))
    op.add_column("review_sentences", sa.Column("segment_kind", sa.String(24), nullable=False,
                                                  server_default="sentence"))
    op.add_column("review_checks", sa.Column("ordinal", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("review_checks", sa.Column("category", sa.String(32), nullable=False,
                                               server_default="function_result"))
    op.add_column("review_checks", sa.Column("source_quote", sa.Text(), nullable=False,
                                               server_default=""))
    op.add_column("review_evidence", sa.Column("candidate_id", sa.String(36), nullable=True))
    op.add_column("review_evidence", sa.Column("validation_status", sa.String(24), nullable=False,
                                                 server_default="unverified"))
    op.create_foreign_key("fk_review_evidence_candidate", "review_evidence", "trace_candidates",
                          ["candidate_id"], ["id"])
    op.create_index("ix_review_evidence_candidate_id", "review_evidence", ["candidate_id"])

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("requirement_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_task_id", sa.String(36), sa.ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_key", sa.String(160), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("review_id", "task_key"),
    )
    op.create_index("ix_review_tasks_review_id", "review_tasks", ["review_id"])
    op.create_index("ix_review_tasks_parent_task_id", "review_tasks", ["parent_task_id"])

    op.create_table(
        "review_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("requirement_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="succeeded"),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_review_events_review_id", "review_events", ["review_id"])
    op.create_index("ix_review_events_task_id", "review_events", ["task_id"])

    op.create_table(
        "review_session_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("requirement_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_kind", sa.String(32), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("review_id", "ordinal"),
    )
    op.create_index("ix_review_session_items_review_id", "review_session_items", ["review_id"])


def downgrade() -> None:
    op.drop_table("review_session_items")
    op.drop_table("review_events")
    op.drop_table("review_tasks")
    op.drop_index("ix_review_evidence_candidate_id", table_name="review_evidence")
    op.drop_constraint("fk_review_evidence_candidate", "review_evidence", type_="foreignkey")
    for table, column in (
        ("review_evidence", "validation_status"),
        ("review_evidence", "candidate_id"),
        ("review_checks", "source_quote"),
        ("review_checks", "category"),
        ("review_checks", "ordinal"),
        ("review_sentences", "segment_kind"),
        ("requirement_reviews", "tlr_result_fingerprint"),
        ("requirement_reviews", "summary_json"),
        ("requirement_reviews", "plan_json"),
        ("requirement_reviews", "snapshot_json"),
    ):
        op.drop_column(table, column)
