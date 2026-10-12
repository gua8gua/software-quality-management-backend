"""One read-only, real LLM call for a selected historical failed pair.

Usage: python scripts/smoke_obligation_pair.py CLAIM_ID CANDIDATE_NODE_ID [--first-two]
This does not create an evaluation run, mutate canonical data, or retry.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.api.dependencies import get_llm_provider
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.modules.artifacts.models import ContentNode
from app.modules.evaluation.models import EvaluationClaim
from app.modules.evaluation.runtime import CONTRACTS, SCHEMA_VERSION, StructuredEvaluationRuntime
from app.modules.evaluation.service import EvaluationService
from app.modules.evaluation.spans import build_span_catalog


async def main(claim_id: str, node_id: str, *, first_two: bool = False) -> None:
    settings = get_settings()
    provider = get_llm_provider(settings)
    if provider is None:
        raise RuntimeError("local generative LLM not configured")
    runtime = StructuredEvaluationRuntime(provider, settings)
    async with AsyncSessionLocal() as session:
        claim = await session.get(EvaluationClaim, claim_id)
        node = await session.get(ContentNode, node_id)
        if claim is None or node is None:
            raise ValueError("historical claim or candidate not found")
        spans = build_span_catalog(node, purpose="evidence")
        if first_two:
            spans = spans[:2]
        payload = {
            "obligation": {"type": "interface", "statement": claim.statement,
                           "source_span_ids": claim.source_span_ids},
            "candidate": {"node_id": node.id, "node_type": node.node_type,
                          "heading_path": [],
                          "evidence_spans": [{"span_id": span.id, "text": span.text}
                                             for span in spans]},
            "supporting_context": [],
        }
        budget = await runtime.inspection_budget(payload)
        result = await runtime.inspect_candidate(payload)
        EvaluationService._validate_pair(result, payload)
        contract = CONTRACTS["obligation_candidate"]
        print(json.dumps({
            "claim_id": claim.id, "candidate_node_id": node.id,
            "candidate_span_ids": [span.id for span in spans],
            "prompt_version": contract.prompt_version,
            "prompt_hash": contract.prompt_hash,
            "schema_version": SCHEMA_VERSION,
            "schema_hash": contract.schema_hash,
            "budget": budget.model_dump(),
            "structured_output": result.model_dump(),
            "validator": "passed",
            "real_llm_requests": 1,
            "derived_subset": first_two,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) not in {3, 4} or (len(sys.argv) == 4 and sys.argv[3] != "--first-two"):
        raise SystemExit("usage: smoke_obligation_pair.py CLAIM_ID CANDIDATE_NODE_ID [--first-two]")
    asyncio.run(main(sys.argv[1], sys.argv[2], first_two=len(sys.argv) == 4))
