from typing import Literal

from pydantic import AliasChoices, Field, StrictBool

from app.modules.tlr.schemas import Contract, Identifier


class BuildInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    dataset_id: str = Field(min_length=1, max_length=36)
    requirement_ids: list[Identifier] = Field(
        min_length=1, max_length=5000, validation_alias=AliasChoices("source_ids", "requirement_ids")
    )
    max_depth: int = Field(default=2, ge=1, le=3)
    section_chars: int = Field(default=10000, ge=1000, le=20000)
    max_units: int = Field(default=500, ge=10, le=2000)
    cluster_chars: int = Field(default=40000, ge=2000, le=100000)


class ChildProposal(Contract):
    quote: str = Field(min_length=1, max_length=20000)
    normalized: str = Field(min_length=1, max_length=2000)
    condition_quotes: list[str] = Field(default_factory=list, max_length=12)
    can_split_further: StrictBool = False


class Decomposition(Contract):
    state: Literal["split", "indivisible", "ambiguous"]
    summary: str = Field(min_length=1, max_length=2500)
    reason: str = Field(min_length=1, max_length=2500)
    children: list[ChildProposal] = Field(default_factory=list, max_length=30)


class Subcategory(Contract):
    key: Identifier
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    section_ids: list[str] = Field(min_length=1, max_length=2000)


class Category(Contract):
    key: Identifier
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    subcategories: list[Subcategory] = Field(min_length=1, max_length=30)


class ClassificationTree(Contract):
    categories: list[Category] = Field(default_factory=list, max_length=30)
    unassigned_section_ids: list[str] = Field(default_factory=list, max_length=2000)
    reason: str = Field(default="", max_length=3000)
