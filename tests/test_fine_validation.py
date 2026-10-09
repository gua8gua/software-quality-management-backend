import pytest

from app.core.error_codes import ErrorCode
from app.core.exceptions import LLMProviderError
from app.modules.artifacts.models import ContentNode
from app.modules.traceability.evidence import EvidencePackage, SupportingItem
from app.modules.traceability.validation import (
    DirectTargetEvidenceMissingError,
    EvidenceNodeOutsidePackageError,
    EvidenceQuoteMismatchError,
    FineTraceValidator,
    UnparsedPictureEvidenceError,
)
from app.providers.llm.base import LLMProvider


class ScriptedLLM(LLMProvider):
    name = "scripted"
    model = "scripted"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.requests = []

    async def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def chat_json(self, **kwargs):
        self.requests.append(kwargs)
        result = self.outputs[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def _package() -> EvidencePackage:
    return EvidencePackage(
        source_requirement={"node_id": "source", "heading_path": [], "original_content": "req"},
        candidate_design_node={
            "node_id": "target",
            "node_type": "paragraph",
            "evidence_status": "available",
            "heading_path": [],
            "original_content": "candidate text",
        },
        supporting_context=[
            SupportingItem(
                node_id="support",
                node_type="paragraph",
                heading_path=[],
                content="actual implementation",
                reason="direct sibling",
                relation_type="sibling",
                origin_node_id="target",
            )
        ],
    )


def _nodes():
    return {
        "target": ContentNode(
            id="target", artifact_id="a", node_type="paragraph", original_type="text", text="candidate text"
        ),
        "support": ContentNode(
            id="support", artifact_id="a", node_type="paragraph", original_type="text", text="actual implementation"
        ),
    }


@pytest.mark.asyncio
async def test_support_only_direct_is_validation_failure_not_semantic_uncertainty():
    invalid = {
        "relation": "direct",
        "evidence": [{"source_node_id": "support", "quote": "actual implementation"}],
        "explanation": "support implements it",
    }
    llm = ScriptedLLM([invalid])
    with pytest.raises(DirectTargetEvidenceMissingError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_target_direct_evidence_is_accepted_without_retry():
    llm = ScriptedLLM(
        [
            {
                "relation": "direct",
                "evidence": [{"source_node_id": "target", "quote": "candidate text"}],
                "explanation": "candidate implements it",
            }
        ]
    )
    output = await FineTraceValidator(llm).validate(_package(), _nodes(), [])
    assert output.relation == "direct"
    assert llm.calls == 1
    assert "diagnostics" not in llm.requests[0]["response_schema"]["properties"]


@pytest.mark.asyncio
async def test_direct_without_any_evidence_is_validation_failure():
    invalid = {"relation": "direct", "evidence": [], "explanation": "unsupported direct"}
    llm = ScriptedLLM([invalid])
    with pytest.raises(DirectTargetEvidenceMissingError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_non_verbatim_quote_is_validation_failure_for_this_attempt():
    llm = ScriptedLLM(
        [
            {
                "relation": "direct",
                "evidence": [{"source_node_id": "target", "quote": "candidate-text"}],
                "explanation": "normalized punctuation",
            },
        ]
    )
    with pytest.raises(EvidenceQuoteMismatchError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_non_verbatim_quote_never_becomes_uncertain():
    invalid = {
        "relation": "direct",
        "evidence": [{"source_node_id": "target", "quote": "candidate-text"}],
        "explanation": "normalized punctuation",
    }
    llm = ScriptedLLM([invalid])
    with pytest.raises(EvidenceQuoteMismatchError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])


@pytest.mark.asyncio
async def test_node_outside_package_is_validation_failure():
    invalid = {
        "relation": "direct",
        "evidence": [{"source_node_id": "invented", "quote": "invented evidence"}],
        "explanation": "invalid node reference",
    }
    llm = ScriptedLLM([invalid])
    with pytest.raises(EvidenceNodeOutsidePackageError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])


@pytest.mark.asyncio
async def test_invalid_json_remains_execution_failure():
    invalid = LLMProviderError("invalid JSON", code=ErrorCode.LLM_NO_RESPONSE)
    llm = ScriptedLLM([invalid])
    with pytest.raises(LLMProviderError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_transport_llm_error_remains_a_hard_failure():
    llm = ScriptedLLM([LLMProviderError("service unavailable", code=ErrorCode.LLM_ERROR)])
    with pytest.raises(LLMProviderError):
        await FineTraceValidator(llm).validate(_package(), _nodes(), [])
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_unparsed_picture_key_evidence_can_remain_uncertain():
    package = _package()
    package.supporting_context.append(
        SupportingItem(
            node_id="picture",
            node_type="picture",
            heading_path=[],
            content="[unparsed picture: visual evidence unavailable]",
            reason="canonical relation",
            relation_type="explicit_reference",
            origin_node_id="target",
            status="visual_content_unparsed",
        )
    )
    nodes = _nodes()
    nodes["picture"] = ContentNode(
        id="picture", artifact_id="a", node_type="picture", original_type="picture"
    )
    llm = ScriptedLLM(
        [{"relation": "uncertain", "evidence": [], "explanation": "key evidence is visual"}]
    )
    output = await FineTraceValidator(llm).validate(package, nodes, [])
    assert output.relation == "uncertain"
    assert package.supporting_context[-1].status == "visual_content_unparsed"


@pytest.mark.asyncio
async def test_picture_cannot_be_used_as_textual_direct_evidence():
    package = _package()
    package.supporting_context.append(
        SupportingItem(
            node_id="picture",
            node_type="picture",
            heading_path=[],
            content="placeholder",
            reason="relation",
            relation_type="picture",
            origin_node_id="target",
            status="visual_content_unparsed",
        )
    )
    nodes = _nodes()
    nodes["picture"] = ContentNode(
        id="picture", artifact_id="a", node_type="picture", original_type="picture"
    )
    invalid = {
        "relation": "direct",
        "evidence": [{"source_node_id": "picture", "quote": "placeholder"}],
        "explanation": "visual",
    }
    llm = ScriptedLLM([invalid])
    with pytest.raises(UnparsedPictureEvidenceError):
        await FineTraceValidator(llm).validate(package, nodes, [])
    assert llm.calls == 1
