from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ArtifactInput(Contract):
    external_id: Identifier
    kind: Identifier
    revision: Identifier
    content: str = Field(min_length=1, max_length=500_000, pattern=r"\S")
    locator: str = Field(default="", max_length=2000)
    structure: dict = Field(default_factory=dict)


class DatasetInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    version: Identifier
    provenance: dict = Field(default_factory=dict)
    artifacts: list[ArtifactInput] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [a.external_id for a in self.artifacts]
        if len(ids) != len(set(ids)):
            raise ValueError("external_id must be unique within a dataset snapshot")
        if sum(len(a.content) for a in self.artifacts) > 20_000_000:
            raise ValueError("dataset exceeds 20 million characters")
        return self


class DatasetView(Contract):
    id: str
    tenant_id: str
    project_id: str
    version: str
    digest: str
    provenance: dict
    created_at: datetime


class ArtifactView(ArtifactInput):
    id: str
    dataset_id: str
    sha256: str
    original_file_id: str | None = None


Strategy = Literal[
    "artifact",
    "chunk",
    "sentence",
    "method",
    "class",
    "sections",
    "model_features",
    "llm_structure",
    "auto",
]


class RunOptions(Contract):
    top_k: int = Field(default=20, ge=1, le=100)
    source_preprocessor: Strategy = "artifact"
    target_preprocessor: Strategy = "artifact"
    kind_preprocessors: dict[str, Strategy] = Field(default_factory=dict)
    code_language: Literal["auto", "java", "python"] = "auto"
    chunk_size: int = Field(default=2000, ge=100, le=20_000)
    max_element_chars: int = Field(default=30_000, ge=100, le=100_000)
    # Explicit backend adaptation; not a claim of original LiSSA numerical equivalence.
    retrieval_backend: Literal["python", "lissa"] = "python"


class RunInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    dataset_id: str = Field(min_length=1, max_length=36)
    source_ids: list[Identifier] = Field(min_length=1, max_length=5000)
    target_ids: list[Identifier] = Field(min_length=1, max_length=5000)
    options: RunOptions = Field(default_factory=RunOptions)
    plan_id: str | None = None
    layer_pair: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def disjoint_unique_ids(self):
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("duplicate source identifiers")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("duplicate target identifiers")
        if set(self.source_ids) & set(self.target_ids):
            raise ValueError("source and target sets must be disjoint")
        return self


class Decision(Contract):
    related: StrictBool
    evidence: str = Field(min_length=1, max_length=2000)
    source_quote: str = Field(default="", max_length=2000)
    target_quote: str = Field(default="", max_length=2000)


class RunView(Contract):
    id: str
    tenant_id: str
    project_id: str
    dataset_id: str
    status: str
    stage: str
    config: dict
    manifest: dict
    counts: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class ElementView(Contract):
    id: str
    artifact_id: str
    external_id: str
    role: str
    kind: str
    ordinal: int
    start: int
    end: int
    content: str
    sha256: str
    embedding: list[float] | None
    processing: dict = Field(default_factory=dict)


class CandidateView(Contract):
    id: str
    source_element_id: str
    target_element_id: str
    rank: int
    similarity: float
    decision: str
    evidence: dict | None


class LinkView(Contract):
    id: str
    source_artifact_id: str
    target_artifact_id: str
    relation: str
    evidence_candidate_ids: list[str]


class Page(Contract):
    items: list[dict]
    offset: int
    limit: int
    total: int


class GoldLink(Contract):
    source_id: Identifier
    target_id: Identifier


class EvaluationInput(Contract):
    gold_links: list[GoldLink] = Field(max_length=500_000)
    provenance: dict = Field(default_factory=dict)


class EvaluationView(Contract):
    id: str
    run_id: str
    metrics: dict
    provenance: dict
