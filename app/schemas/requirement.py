from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RequirementType = Literal["functional", "non_functional", "business_rule", "constraint"]
Priority = Literal["high", "medium", "low", "unspecified"]


class RequirementCandidate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=5000)
    requirement_type: RequirementType
    priority: Priority = "unspecified"
    actor: str | None = Field(default=None, max_length=255)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    source_quote: str = Field(min_length=1, max_length=5000)
    source_location: str | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)


class RequirementExtractionResult(BaseModel):
    requirements: list[RequirementCandidate] = Field(max_length=100)


class RequirementExtractionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    document_id: str = Field(min_length=1, max_length=36)
    content: str = Field(min_length=1, max_length=2_000_000)
    source_location: str | None = Field(default=None, max_length=500)
