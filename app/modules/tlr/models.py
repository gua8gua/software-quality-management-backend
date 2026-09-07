from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


class TlrDataset(Base):
    __tablename__ = "tlr_datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TlrProject(Base):
    __tablename__ = "tlr_projects"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TlrFile(Base):
    __tablename__ = "tlr_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[bytes] = mapped_column(LargeBinary)


class TlrArtifact(Base):
    __tablename__ = "tlr_artifacts"
    __table_args__ = (UniqueConstraint("dataset_id", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("tlr_datasets.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(128))
    revision: Mapped[str] = mapped_column(String(128))
    locator: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    original_file_id: Mapped[str | None] = mapped_column(ForeignKey("tlr_files.id"))
    structure: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class TlrHierarchyNode(Base):
    """Persisted dataset hierarchy; artifact_id=None denotes a pure structure node."""

    __tablename__ = "tlr_hierarchy_nodes"
    __table_args__ = (UniqueConstraint("dataset_id", "node_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("tlr_datasets.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("tlr_hierarchy_nodes.id"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("tlr_artifacts.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(500))
    node_type: Mapped[str] = mapped_column(String(64), default="group")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class TlrRun(Base):
    __tablename__ = "tlr_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="ck_tlr_run_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("tlr_datasets.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    stage: Mapped[str] = mapped_column(String(32), default="created")
    config: Mapped[dict] = mapped_column(JSON)
    manifest: Mapped[dict] = mapped_column(JSON)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TlrElement(Base):
    __tablename__ = "tlr_elements"
    __table_args__ = (UniqueConstraint("run_id", "artifact_id", "role", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("tlr_artifacts.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(128))
    ordinal: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list | None] = mapped_column(JSON)
    processing: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class TlrCandidate(Base):
    __tablename__ = "tlr_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "source_element_id", "target_element_id"),
        CheckConstraint("decision IN ('pending','related','unrelated')", name="ck_tlr_decision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id"), index=True)
    source_element_id: Mapped[str] = mapped_column(ForeignKey("tlr_elements.id"))
    target_element_id: Mapped[str] = mapped_column(ForeignKey("tlr_elements.id"))
    rank: Mapped[int] = mapped_column(Integer)
    similarity: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(20), default="pending")
    evidence: Mapped[dict | None] = mapped_column(JSON)


class TlrLink(Base):
    __tablename__ = "tlr_links"
    __table_args__ = (UniqueConstraint("run_id", "source_artifact_id", "target_artifact_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id"), index=True)
    source_artifact_id: Mapped[str] = mapped_column(ForeignKey("tlr_artifacts.id"))
    target_artifact_id: Mapped[str] = mapped_column(ForeignKey("tlr_artifacts.id"))
    relation: Mapped[str] = mapped_column(String(32), default="related_to")
    evidence_candidate_ids: Mapped[list] = mapped_column(JSON)


class TlrEvaluation(Base):
    __tablename__ = "tlr_evaluations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("tlr_runs.id"), index=True)
    gold_links: Mapped[list] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)


TLR_TABLES = [
    m.__table__
    for m in (
        TlrProject,
        TlrFile,
        TlrDataset,
        TlrArtifact,
        TlrHierarchyNode,
        TlrRun,
        TlrElement,
        TlrCandidate,
        TlrLink,
        TlrEvaluation,
    )
]

# Local SQLite/test initialization follows the complete TLR execution dependency graph.
from app.modules.model_config.models import MODEL_CONFIG_TABLES  # noqa: E402

TLR_TABLES = [*MODEL_CONFIG_TABLES, *TLR_TABLES]
