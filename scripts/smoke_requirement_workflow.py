"""Small isolated end-to-end requirement hierarchy/TLR/consistency contract smoke.

Synthetic embeddings and responses verify plumbing, not semantic accuracy or performance.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.modules.consistency.judge import conclude, parse_judgement
from app.modules.consistency.models import ConsistencyReview, ConsistencyRun
from app.modules.consistency.schemas import AnalysisInput, Judgement
from app.modules.consistency.service import ConsistencyService
from app.modules.requirements.models import RequirementAnalysis
from app.modules.requirements.schemas import BuildInput
from app.modules.requirements.service import RequirementService
from app.modules.tlr.models import TLR_TABLES
from app.modules.tlr.schemas import ArtifactInput, DatasetInput, RunInput, RunOptions
from app.modules.tlr.service import TlrService
from app.providers.embedding.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from scripts.run_consistency import markdown


class Vectors(EmbeddingProvider):
    model = "synthetic-routing-vectors"

    async def embed(self, texts):
        return [
            [1.0, 0.0, 0.1]
            if any(w in t.lower() for w in ("blank", "trim", "name"))
            else [0.0, 1.0, 0.1]
            if "audit" in t.lower()
            else [0.5, 0.5, 1.0]
            for t in texts
        ]


class Responses(LLMProvider):
    model = "synthetic-hierarchy-contract"

    async def chat_json(self, *, user_payload, **kwargs):
        schema = user_payload.get("output_schema", {}).get("title")
        if schema == "Decomposition":
            text = user_payload["text"]
            if "quickly" in text:
                return dict(
                    state="ambiguous", summary="Performance", reason="Missing bound", children=[]
                )
            children = []
            if text == "Reject blank names. Trim whitespace.":
                children = [
                    dict(quote=q, normalized=q, condition_quotes=[], can_split_further=False)
                    for q in ("Reject blank names.", "Trim whitespace.")
                ]
            return dict(
                state="split" if children else "indivisible",
                summary=text,
                reason="fixture",
                children=children,
            )
        if schema == "ClassificationTree":
            items = user_payload["sections"]
            cats = []
            for key, token in (("names", "names"), ("audit", "audit")):
                ids = [s["id"] for s in items if token in s["summary"].lower()]
                cats.append(
                    dict(
                        key=key,
                        title=key,
                        summary=key,
                        subcategories=[
                            dict(key=key + "-sub", title=key, summary=key, section_ids=ids)
                        ],
                    )
                )
            return dict(
                categories=cats,
                unassigned_section_ids=[s["id"] for s in items if "Performance" in s["summary"]],
                reason="fixture",
            )
        if "source" in user_payload:
            return dict(
                related=True,
                evidence="synthetic relation",
                source_quote=user_payload["source"]["content"][:40],
                target_quote=user_payload["target"]["content"][:40],
            )
        packet = user_payload["packet"]
        units = packet["requirement_units"]
        citations = [
            dict(evidence_id=e["id"], quote=e["text"], start=0)
            for e in packet["evidence"]
            if e["kind"] == "code"
        ]
        records = []
        for n in units:
            status = (
                "conflict"
                if "Trim" in n["text"]
                else "judgement_error"
                if "quickly" in n["text"]
                else "implemented"
            )
            records.append(
                dict(
                    citations=citations,
                    parts=[
                        dict(
                            unit_id=n["id"],
                            requirement_quote=n["text"],
                            implemented_behavior="synthetic code contribution",
                            outcomes=[status],
                            reason="fixture",
                            scope_complete=False,
                        )
                    ],
                )
            )
        # Preserve a grounded conflict alongside another independently unjudgeable portion.
        if packet["requirement"]["id"] == "R1":
            records.append(
                dict(
                    citations=[],
                    parts=[
                        dict(
                            unit_id=None,
                            requirement_quote=units[0]["text"],
                            implemented_behavior="",
                            outcomes=["judgement_error"],
                            reason="synthetic unresolved context",
                        )
                    ],
                )
            )
        return dict(
            requirement_clarity="clear",
            evidence_sufficiency="sufficient",
            covers_whole_requirement=False,
            implementations=records,
            missing_context=[],
            requested_artifact_ids=[],
            summary="fixture",
        )


async def main():
    root = Path("artifacts/requirements/smoke") / str(uuid4())
    root.mkdir(parents=True)
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + (root / "workflow.sqlite").absolute().as_posix()
    )
    metadata = MetaData()
    for table in [
        *TLR_TABLES,
        RequirementAnalysis.__table__,
        ConsistencyRun.__table__,
        ConsistencyReview.__table__,
    ]:
        table.to_metadata(metadata)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            settings = get_settings()
            llm = Responses()
            embedding = Vectors()
            tlr = TlrService(session, embedding, llm, settings)
            data = DatasetInput(
                tenant_id="smoke",
                project_id="hierarchy",
                version="1",
                artifacts=[
                    ArtifactInput(
                        external_id="R1",
                        kind="requirement",
                        revision="1",
                        content="Reject blank names. Trim whitespace.",
                    ),
                    ArtifactInput(
                        external_id="R2",
                        kind="requirement",
                        revision="1",
                        content="Keep audit records.",
                    ),
                    ArtifactInput(
                        external_id="R3",
                        kind="requirement",
                        revision="1",
                        content="Respond quickly.",
                    ),
                    ArtifactInput(
                        external_id="name_code",
                        kind="code",
                        revision="1",
                        locator="names.py",
                        content=(
                            "def name(value):\n    if not value:\n"
                            '        raise ValueError("blank")\n    return value\n'
                        ),
                    ),
                    ArtifactInput(
                        external_id="audit_code",
                        kind="code",
                        revision="1",
                        locator="audit.py",
                        content="def audit(event):\n    return str(event)\n",
                    ),
                ],
            )
            dataset = await tlr.import_dataset(data)
            hierarchy = await RequirementService(session, settings, llm).build(
                BuildInput(
                    tenant_id="smoke",
                    project_id="hierarchy",
                    dataset_id=dataset.id,
                    requirement_ids=["R1", "R2", "R3"],
                )
            )
            run = await tlr.create_run(
                RunInput(
                    tenant_id="smoke",
                    project_id="hierarchy",
                    dataset_id=dataset.id,
                    source_ids=["R1", "R2", "R3"],
                    target_ids=["name_code", "audit_code"],
                    options=RunOptions(
                        requirements_run_id=hierarchy["id"], top_k=2, hierarchy_probe_targets=0
                    ),
                )
            )
            run = await tlr.execute(run.id, "smoke", "hierarchy")
            assert run.status == "completed"
            stats = run.manifest["hierarchical_retrieval"]
            assert stats["evaluated_pairs"] < stats["full_pairs"]
            assert hierarchy["result"]["categories"]["unassigned_section_ids"]
            service = ConsistencyService(session, settings, llm)
            analysis = await service.create(
                AnalysisInput(tenant_id="smoke", project_id="hierarchy", tlr_run_id=run.id)
            )
            await service.execute(analysis.id, "smoke", "hierarchy")
            report = await service.view(analysis)
            assert report["status"] == "completed", report["results"]
            first = report["results"]["requirements"]["R1"]
            assert {"conflict", "judgement_error"} <= set(first["outcomes"])
            assert "findings" not in first
            packet = first["packet"]
            raw = await llm.chat_json(user_payload={"packet": packet})
            raw["implementations"].append({"parts": [{"outcomes": ["invalid"]}]})
            retained = conclude(packet, parse_judgement(raw, packet))
            assert {"conflict", "judgement_error"} <= set(retained["outcomes"])
            raw = await llm.chat_json(user_payload={"packet": packet})
            raw["implementations"][0]["citations"][0]["quote"] = "fabricated"
            checked = conclude(packet, Judgement.model_validate(raw))
            assert "judgement_error" in checked["outcomes"]
            (root / "requirements.json").write_text(
                json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (root / "tlr.json").write_text(
                json.dumps(run.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (root / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (root / "report.md").write_text(markdown(report), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "output": str(root),
                        "pairs": [stats["evaluated_pairs"], stats["full_pairs"]],
                        "outcomes": {
                            k: r["outcomes"] for k, r in report["results"]["requirements"].items()
                        },
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
