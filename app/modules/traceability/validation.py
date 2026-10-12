from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.context_budget import ContextBudgetManager
from app.modules.artifacts.models import ContentNode, ContentRelation
from app.modules.traceability.evidence import EvidencePackage
from app.modules.traceability.projections import node_content
from app.modules.traceability.prompts import FINE_TRACE_SYSTEM_PROMPT
from app.providers.llm.base import LLMProvider


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_node_id: str
    quote: str = Field(min_length=1)


class FineTraceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relation: Literal["direct", "related", "no_link", "uncertain"]
    evidence: list[EvidenceQuote] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    diagnostics: list[str] = Field(default_factory=list)


class FineTraceModelOutput(BaseModel):
    """Structured fields the model may supply; diagnostics remain application-owned."""

    model_config = ConfigDict(extra="forbid")
    relation: Literal["direct", "related", "no_link", "uncertain"]
    evidence: list[EvidenceQuote] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class EvidenceValidationError(ValueError):
    """A model-supplied evidence item violates a canonical evidence boundary."""

    diagnostic = "evidence_validation_failed"


class EvidenceQuoteMismatchError(EvidenceValidationError):
    diagnostic = "evidence_quote_validation_failed"


class EvidenceNodeOutsidePackageError(EvidenceValidationError):
    diagnostic = "evidence_node_outside_package"


class UnparsedPictureEvidenceError(EvidenceValidationError):
    diagnostic = "unparsed_picture_used_as_text_evidence"


class DirectTargetEvidenceMissingError(EvidenceValidationError):
    diagnostic = "direct_target_evidence_missing"


class FineTraceValidator:
    def __init__(self, llm: LLMProvider, *, max_tokens: int = 768,
                 settings: Settings | None = None):
        self.llm = llm
        self.max_tokens = max_tokens
        settings = settings or Settings()
        self.budget = ContextBudgetManager(
            llm, context_window=settings.llm_context_window,
            safety_margin=settings.llm_context_safety_tokens,
        )

    async def validate(
        self,
        package: EvidencePackage,
        nodes: dict[str, ContentNode],
        relations: list[ContentRelation],
    ) -> FineTraceOutput:
        output = await self._request(package.as_prompt())
        self._validate_quotes(output, package, nodes, relations)
        target_id = package.candidate_design_node["node_id"]
        if output.relation == "direct" and not any(
            evidence.source_node_id == target_id for evidence in output.evidence
        ):
            raise DirectTargetEvidenceMissingError(
                "direct judgement has no validated quote from the candidate node itself"
            )
        return output

    async def _request(self, payload: dict) -> FineTraceOutput:
        await self.budget.ensure_fits(
            system_prompt=FINE_TRACE_SYSTEM_PROMPT,
            user_payload=payload,
            response_schema=FineTraceModelOutput.model_json_schema(),
            history=[], output_reserve=self.max_tokens,
        )
        result = await self.llm.chat_json(
            system_prompt=FINE_TRACE_SYSTEM_PROMPT,
            user_payload=payload,
            response_schema=FineTraceModelOutput.model_json_schema(),
            temperature=0.0,
            max_tokens=self.max_tokens,
        )
        model_output = FineTraceModelOutput.model_validate(result)
        return FineTraceOutput(**model_output.model_dump())

    @staticmethod
    def _validate_quotes(
        output: FineTraceOutput,
        package: EvidencePackage,
        nodes: dict[str, ContentNode],
        relations: list[ContentRelation],
    ) -> None:
        allowed = {package.candidate_design_node["node_id"]} | {
            item.node_id for item in package.supporting_context
        }
        for evidence in output.evidence:
            if evidence.source_node_id not in allowed:
                raise EvidenceNodeOutsidePackageError(
                    "fine-screening evidence references a node outside the package"
                )
            source = nodes[evidence.source_node_id]
            if source.node_type == "picture":
                raise UnparsedPictureEvidenceError(
                    "unparsed picture cannot be used as textual direct evidence"
                )
            searchable = node_content(source, nodes, relations)
            if evidence.quote not in searchable:
                raise EvidenceQuoteMismatchError(
                    "fine-screening evidence quote is not present in canonical content"
                )
