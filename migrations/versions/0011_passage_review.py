"""Evidence-first review checkpoints and shared quotation/check associations."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0011_passage_review"
down_revision = "0010_audit_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_sentences", sa.Column("checkset_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("review_sentences", sa.Column("checks_finalized", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("review_evidence", sa.Column("review_id", sa.String(36), nullable=True))
    op.create_table(
        "review_evidence_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_id", sa.String(36), sa.ForeignKey("review_evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_id", sa.String(36), sa.ForeignKey("review_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.UniqueConstraint("evidence_id", "check_id", "relation"),
    )
    for field in ("evidence_id", "check_id"):
        op.create_index(f"ix_review_evidence_checks_{field}", "review_evidence_checks", [field])
    connection = op.get_bind()
    # Preserve old evidence IDs and their known associations, never infer old
    # reads to be passage assessments or claim that an old check set is complete.
    rows = list(connection.execute(sa.text(
        "SELECT e.id, e.check_id, e.relation, s.review_id FROM review_evidence e "
        "JOIN review_checks c ON c.id = e.check_id JOIN review_sentences s ON s.id = c.sentence_id"
    )).mappings())
    for row in rows:
        connection.execute(sa.text("UPDATE review_evidence SET review_id = :review_id WHERE id = :id"),
                           {"review_id": row["review_id"], "id": row["id"]})
        connection.execute(sa.text(
            "INSERT INTO review_evidence_checks (id, evidence_id, check_id, relation) "
            "VALUES (:id, :evidence_id, :check_id, :relation)"),
            {"id": str(uuid4()), "evidence_id": row["id"], "check_id": row["check_id"], "relation": row["relation"]})
    old_fk = next(fk for fk in sa.inspect(connection).get_foreign_keys("review_evidence")
                  if fk["constrained_columns"] == ["check_id"])
    fk_name = old_fk["name"] or "fk_review_evidence_check_id_review_checks"
    with op.batch_alter_table("review_evidence", naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}) as batch:
        batch.alter_column("review_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key("fk_review_evidence_review_id", "requirement_reviews", ["review_id"], ["id"], ondelete="CASCADE")
        batch.drop_index("ix_review_evidence_check_id")
        batch.drop_constraint(fk_name, type_="foreignkey")
        batch.drop_column("check_id")
        batch.drop_column("relation")
        batch.create_index("ix_review_evidence_review_id", ["review_id"])
    op.create_table(
        "review_passages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sentence_id", sa.String(36), sa.ForeignKey("review_sentences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_node_id", sa.String(36), sa.ForeignKey("content_nodes.id"), nullable=False),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("trace_candidates.id"), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("checkset_version", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("sentence_id", "design_node_id", "start_offset", "end_offset", "checkset_version"),
    )
    for field in ("sentence_id", "design_node_id"):
        op.create_index(f"ix_review_passages_{field}", "review_passages", [field])
    op.create_table(
        "review_passage_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("passage_id", sa.String(36), sa.ForeignKey("review_passages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_id", sa.String(36), sa.ForeignKey("review_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("resulting_status", sa.String(24), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.UniqueConstraint("passage_id", "check_id"),
    )
    for field in ("passage_id", "check_id"):
        op.create_index(f"ix_review_passage_assessments_{field}", "review_passage_assessments", [field])


def downgrade() -> None:
    # M:N associations cannot be restored to one check_id without losing facts.
    raise RuntimeError("Destructive downgrade would lose shared evidence associations; restore a database backup instead")
