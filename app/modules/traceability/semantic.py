from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.context_budget import ContextBudgetManager
from app.core.execution import fingerprint_payload, prompt_hash, schema_hash
from app.modules.artifacts.models import Artifact, ContentNode, ContentRelation
from app.modules.traceability.models import SemanticAnnotation
from app.modules.traceability.projections import document_type, heading_path, node_content
from app.modules.traceability.prompts import (
    SEMANTIC_ANNOTATION_PROMPT_VERSION,
    SEMANTIC_ANNOTATION_SYSTEM_PROMPT,
)
from app.providers.llm.base import LLMProvider


class AnnotationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["primary", "context", "exclude", "uncertain"]
    semantic_synopsis: str = Field(min_length=1)


class SemanticAnnotator:
    def __init__(self, llm: LLMProvider, *, generation_version: str,
                 settings: Settings | None = None):
        self.llm = llm
        self.generation_version = generation_version
        settings = settings or Settings()
        self.budget = ContextBudgetManager(
            llm, context_window=settings.llm_context_window,
            safety_margin=settings.llm_context_safety_tokens,
        )

    async def annotate(
        self,
        run_id: str,
        artifact: Artifact,
        node: ContentNode,
        nodes: dict[str, ContentNode],
        relations: list[ContentRelation],
    ) -> SemanticAnnotation:
        payload = {
            "document_type": document_type(artifact),
            "full_heading_path": heading_path(node, nodes),
            "node_type": node.node_type,
            "node_content": node_content(node, nodes, relations),
        }
        await self.budget.ensure_fits(
            system_prompt=SEMANTIC_ANNOTATION_SYSTEM_PROMPT,
            user_payload=payload, response_schema=AnnotationOutput.model_json_schema(),
            history=[], output_reserve=640,
        )
        result = await self.llm.chat_json(
            system_prompt=SEMANTIC_ANNOTATION_SYSTEM_PROMPT,
            user_payload=payload,
            response_schema=AnnotationOutput.model_json_schema(),
            temperature=0.0,
            max_tokens=640,
        )
        annotation = AnnotationOutput.model_validate(result)
        return SemanticAnnotation(
            run_id=run_id,
            node_id=node.id,
            document_type=document_type(artifact),
            role=annotation.role,
            semantic_synopsis=annotation.semantic_synopsis,
            synopsis_status="completed",
            synopsis_error=None,
            provider=self.llm.name,
            model=self.llm.model,
            model_version=self.llm.model_version,
            prompt_version=SEMANTIC_ANNOTATION_PROMPT_VERSION,
            input_fingerprint=fingerprint_payload(
                payload, self.llm.name, self.llm.model, self.llm.model_version,
                prompt_hash(SEMANTIC_ANNOTATION_SYSTEM_PROMPT),
                schema_hash(AnnotationOutput.model_json_schema()),
            ),
            generation_version=self.generation_version,
        )
