from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RunOptions(Contract):
    dense_top_k: int = Field(default=20, ge=1, le=200)
    bm25_top_k: int = Field(default=20, ge=1, le=200)
    fine_top_k: int = Field(default=20, ge=1, le=200)
    fine_concurrency: int = Field(default=16, ge=1, le=16)
    fine_max_tokens: int = Field(default=768, ge=64, le=8192)
    sibling_window: int = Field(default=1, ge=0, le=5)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    generation_version: str = Field(default="tlr-joint-annotation-v1", min_length=1, max_length=64)
    tokenizer_user_terms: list[str] = Field(default_factory=list, max_length=100)


class RunCreate(Contract):
    tenant_id: str = "local"
    project_id: str
    source_artifact_ids: list[str] = Field(min_length=1)
    target_artifact_ids: list[str] = Field(min_length=1)
    task_name: str = Field(default="需求—设计追溯", min_length=1, max_length=200)
    task_description: str = Field(default="", max_length=2000)
    options: RunOptions = Field(default_factory=RunOptions)


class RunView(Contract):
    id: str
    tenant_id: str
    project_id: str
    status: str
    result_status: str
    stage: str
    config: dict
    metrics: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class SemanticAnnotationView(Contract):
    id: str
    run_id: str
    node_id: str
    document_type: str
    role: str
    semantic_synopsis: str | None
    synopsis_status: str
    synopsis_error: str | None
    provider: str
    model: str
    model_version: str
    prompt_version: str
    generation_version: str


class RetrievalProjectionView(Contract):
    id: str
    run_id: str
    node_id: str
    channel: str
    document_type: str
    heading_path: list
    text: str
    embedding_provider: str | None
    embedding_model: str | None
    embedding_model_version: str | None


class TraceUnitView(Contract):
    id: str
    run_id: str
    artifact_id: str
    node_id: str
    annotation_id: str
    raw_projection_id: str
    synopsis_projection_id: str | None
    side: str
    semantic_role: str
    ordinal: int


class CandidateView(Contract):
    id: str
    run_id: str
    source_unit_id: str
    target_unit_id: str
    dense_rank: int | None
    dense_score: float | None
    synopsis_dense_rank: int | None
    synopsis_dense_score: float | None
    bm25_rank: int | None
    bm25_score: float | None
    rrf_score: float
    fused_rank: int | None
    retrieval_sources: list
    structural_reasons: list
    reranker_rank: int | None
    reranker_score: float | None
    final_relation: str | None
    evidence_source_node_ids: list
    evidence: dict | None
    explanation: str
    diagnostics: dict
    execution_status: str
    validation_status: str
    error_category: str | None
    error_code: str | None
    attempt_count: int
    last_error: str | None
    validator_provider: str | None
    validator_model: str | None
    validator_model_version: str | None
    prompt_version: str | None
    schema_version: str | None
    input_fingerprint: str | None


class TraceLinkView(Contract):
    id: str
    run_id: str
    source_artifact_id: str
    target_artifact_id: str
    source_node_id: str
    target_node_id: str
    candidate_id: str
    relation: str
    evidence_source_node_ids: list
    evidence: dict
    explanation: str
    validator_provider: str
    validator_model: str
    validator_model_version: str
    prompt_version: str
