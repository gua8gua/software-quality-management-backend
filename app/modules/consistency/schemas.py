from typing import Literal

from pydantic import AliasChoices, Field, StrictBool, model_validator

from app.modules.tlr.schemas import Contract, Identifier


class AnalysisInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    tlr_run_id: str = Field(min_length=1, max_length=36)
    requirements_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    requirement_ids: list[Identifier] = Field(
        default_factory=list,
        max_length=5000,
        validation_alias=AliasChoices("source_ids", "requirement_ids"),
    )
    analysis_kind: Literal["code", "document"] = "code"
    document_relation: Literal["refinement", "agreement"] = "refinement"
    target_ids: list[Identifier] = Field(default_factory=list, max_length=5000)
    additional_artifact_ids: list[Identifier] = Field(default_factory=list, max_length=50)
    max_evidence_chars: int = Field(default=60000, ge=2000, le=150000)
    max_expansion_rounds: int = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def unique(self):
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("duplicate requirement_ids")
        if len(self.target_ids) != len(set(self.target_ids)):
            raise ValueError("duplicate target_ids")
        if set(self.requirement_ids) & set(self.target_ids):
            raise ValueError("source and target must be disjoint")
        return self


class Citation(Contract):
    evidence_id: str = Field(min_length=1, max_length=256)
    quote: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    start: int = Field(ge=0, description="Unicode character offset within evidence text")


Outcome = Literal["implemented", "missing_functionality", "conflict", "judgement_error"]


class ImplementedPart(Contract):
    unit_id: str | None = Field(default=None, max_length=500)
    requirement_quote: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    implemented_behavior: str = Field(max_length=6000)
    outcomes: list[Outcome] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=6000, pattern=r"\S")
    missing_behavior: str = Field(default="", max_length=4000)
    scope_complete: StrictBool = False


class CodeImplementation(Contract):
    citations: list[Citation] = Field(default_factory=list, max_length=30)
    parts: list[ImplementedPart] = Field(min_length=1, max_length=50)


class Judgement(Contract):
    requirement_clarity: Literal["clear", "ambiguous", "not_verifiable"]
    evidence_sufficiency: Literal["sufficient", "insufficient"]
    covers_whole_requirement: StrictBool
    implementations: list[CodeImplementation] = Field(default_factory=list, max_length=100)
    missing_context: list[str] = Field(default_factory=list, max_length=30)
    verification_notes: list[str] = Field(default_factory=list, max_length=30)
    clarification_questions: list[str] = Field(default_factory=list, max_length=20)
    requested_artifact_ids: list[Identifier] = Field(default_factory=list, max_length=10)
    summary: str = Field(min_length=1, max_length=6000, pattern=r"\S")


class ReviewInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    requirement_id: Identifier
    decision: Literal["confirmed", "rejected", "unresolved"]
    reviewer: str = Field(min_length=1, max_length=200, pattern=r"\S")
    method: Literal["human_review", "test_result", "runtime_trace"]
    evidence: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    evidence_locator: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    snapshot_digest: str = Field(min_length=64, max_length=64)
