"""Formal review facts, separate from Agent conversation history and old evaluation."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


class Review(Base):
    __tablename__ = "requirement_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    tlr_run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id"), index=True)
    requirement_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="open")
    # Snapshot of the TLR/config binding used by this audit.  It is deliberately
    # separate from the live TLR rows so a later run cannot silently change an
    # already-created audit workspace.
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Artifact-level scope selected by the user.  A review row still binds to
    # one canonical source node, but batch creation records the user scope so
    # later evidence queries cannot silently widen to another target artifact.
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tlr_result_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewBatch(Base):
    """One user-visible artifact-level analysis task, with independent reviews."""

    __tablename__ = "review_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    tlr_run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    results_json: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Durable worker fencing.  The token is returned only to the worker that
    # claimed the batch; every subsequent mutation must present it.  This
    # prevents a stale web-process task from completing or interrupting a
    # resumed batch in another process.
    worker_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewSentence(Base):
    __tablename__ = "review_sentences"
    __table_args__ = (UniqueConstraint("review_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("requirement_reviews.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    # The first implementation treats each retained source range as an audit
    # segment/sentence.  No rewritten text is stored.
    segment_kind: Mapped[str] = mapped_column(String(24), default="sentence")
    checkset_version: Mapped[int] = mapped_column(Integer, default=0)
    checks_finalized: Mapped[bool] = mapped_column(Boolean, default=False)


class ReviewCheck(Base):
    __tablename__ = "review_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sentence_id: Mapped[str] = mapped_column(ForeignKey("review_sentences.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    category: Mapped[str] = mapped_column(String(32), default="function_result")
    source_quote: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    note: Mapped[str] = mapped_column(Text, default="")


class ReviewEvidence(Base):
    __tablename__ = "review_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("requirement_reviews.id", ondelete="CASCADE"), index=True)
    design_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    exact_quote: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("trace_candidates.id"), nullable=True, index=True)
    validation_status: Mapped[str] = mapped_column(String(24), default="unverified")


class ReviewEvidenceCheck(Base):
    """One canonical quotation can concern multiple checks; no text is copied here."""

    __tablename__ = "review_evidence_checks"
    __table_args__ = (UniqueConstraint("evidence_id", "check_id", "relation"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("review_evidence.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[str] = mapped_column(ForeignKey("review_checks.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(24))


class ReviewPassage(Base):
    """An exact SDS range and its execution checkpoint, not another TLR unit."""

    __tablename__ = "review_passages"
    __table_args__ = (UniqueConstraint("sentence_id", "design_node_id", "start_offset", "end_offset", "checkset_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sentence_id: Mapped[str] = mapped_column(ForeignKey("review_sentences.id", ondelete="CASCADE"), index=True)
    design_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("trace_candidates.id"), nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    checkset_version: Mapped[int] = mapped_column(Integer)
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewPassageAssessment(Base):
    """Coverage includes irrelevant passages, which are not evidence of absence."""

    __tablename__ = "review_passage_assessments"
    __table_args__ = (UniqueConstraint("passage_id", "check_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    passage_id: Mapped[str] = mapped_column(ForeignKey("review_passages.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[str] = mapped_column(ForeignKey("review_checks.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text)
    resulting_status: Mapped[str] = mapped_column(String(24))
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class ReviewVisit(Base):
    __tablename__ = "review_mandatory_visits"
    __table_args__ = (UniqueConstraint("review_id", "design_node_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("requirement_reviews.id", ondelete="CASCADE"), index=True)
    design_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")


class ReviewTask(Base):
    """Durable, observable audit task state; no hidden model reasoning is stored."""

    __tablename__ = "review_tasks"
    __table_args__ = (UniqueConstraint("review_id", "task_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("requirement_reviews.id", ondelete="CASCADE"), index=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    task_key: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReviewEvent(Base):
    """Observable action/result log for planner, auditor and verifier roles."""

    __tablename__ = "review_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("requirement_reviews.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="succeeded")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewSessionItem(Base):
    """Bounded external session memory; stores calls/results, never chain-of-thought."""

    __tablename__ = "review_session_items"
    __table_args__ = (UniqueConstraint("review_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("requirement_reviews.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    item_kind: Mapped[str] = mapped_column(String(32))
    actor_role: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
