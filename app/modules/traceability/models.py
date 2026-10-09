from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
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


class TlrRun(Base):
    __tablename__ = "tlr_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="ck_tlr_run_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result_status: Mapped[str] = mapped_column(
        String(20), default="not_evaluated", server_default="legacy"
    )
    stage: Mapped[str] = mapped_column(String(32), default="created")
    config: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SemanticAnnotation(Base):
    __tablename__ = "semantic_annotations"
    __table_args__ = (
        UniqueConstraint("run_id", "node_id"),
        CheckConstraint(
            "role IN ('primary','context','exclude','uncertain')",
            name="ck_semantic_annotation_role",
        ),
        CheckConstraint(
            "synopsis_status IN ('completed','failed')",
            name="ck_semantic_annotation_synopsis_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(8))
    role: Mapped[str] = mapped_column(String(16), index=True)
    semantic_synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    synopsis_status: Mapped[str] = mapped_column(String(16), default="completed")
    synopsis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
    model_version: Mapped[str] = mapped_column(String(256))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    generation_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


_embedding_type = VECTOR().with_variant(JSON(), "sqlite")


class RetrievalProjection(Base):
    __tablename__ = "retrieval_projections"
    __table_args__ = (
        UniqueConstraint("run_id", "node_id", "channel"),
        CheckConstraint("channel IN ('raw','synopsis')", name="ck_projection_channel"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    annotation_id: Mapped[str | None] = mapped_column(
        ForeignKey("semantic_annotations.id", ondelete="CASCADE"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    document_type: Mapped[str] = mapped_column(String(8))
    heading_path: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(_embedding_type, nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    embedding_model_version: Mapped[str | None] = mapped_column(String(256), nullable=True)


class TraceUnit(Base):
    __tablename__ = "trace_units"
    __table_args__ = (UniqueConstraint("run_id", "node_id", "side"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    annotation_id: Mapped[str] = mapped_column(ForeignKey("semantic_annotations.id"))
    raw_projection_id: Mapped[str] = mapped_column(ForeignKey("retrieval_projections.id"))
    synopsis_projection_id: Mapped[str | None] = mapped_column(
        ForeignKey("retrieval_projections.id"), nullable=True
    )
    side: Mapped[str] = mapped_column(String(10))
    semantic_role: Mapped[str] = mapped_column(String(16))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)


class Candidate(Base):
    __tablename__ = "trace_candidates"
    __table_args__ = (UniqueConstraint("run_id", "source_unit_id", "target_unit_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id", ondelete="CASCADE"), index=True)
    source_unit_id: Mapped[str] = mapped_column(ForeignKey("trace_units.id"), index=True)
    target_unit_id: Mapped[str] = mapped_column(ForeignKey("trace_units.id"), index=True)
    dense_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dense_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    synopsis_dense_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synopsis_dense_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    bm25_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bm25_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rrf_score: Mapped[float] = mapped_column(Float, default=0.0)
    fused_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_sources: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    structural_reasons: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    reranker_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reranker_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_relation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence_source_node_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, default="", server_default="")
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    execution_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="legacy"
    )
    validation_status: Mapped[str] = mapped_column(
        String(20), default="not_checked", server_default="legacy"
    )
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    validator_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validator_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    validator_model_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class TraceLink(Base):
    __tablename__ = "trace_links"
    __table_args__ = (
        UniqueConstraint("run_id", "source_node_id", "target_node_id"),
        CheckConstraint("relation = 'direct'", name="ck_trace_link_relation"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id", ondelete="CASCADE"), index=True)
    source_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    target_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    source_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    target_node_id: Mapped[str] = mapped_column(ForeignKey("content_nodes.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("trace_candidates.id"))
    relation: Mapped[str] = mapped_column(String(16))
    evidence_source_node_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    explanation: Mapped[str] = mapped_column(Text, default="", server_default="")
    validator_provider: Mapped[str] = mapped_column(String(64))
    validator_model: Mapped[str] = mapped_column(String(256))
    validator_model_version: Mapped[str] = mapped_column(String(256))
    prompt_version: Mapped[str] = mapped_column(String(64))


TRACEABILITY_TABLES = [
    TlrRun.__table__,
    SemanticAnnotation.__table__,
    RetrievalProjection.__table__,
    TraceUnit.__table__,
    Candidate.__table__,
    TraceLink.__table__,
]
