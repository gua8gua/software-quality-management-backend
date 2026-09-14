"""One isolated API/persistence smoke run, optionally using the configured real model.

TLR links are explicit synthetic fixtures, not retrieval accuracy evidence.
The SQLite database and reports stay under artifacts/consistency/smoke/<uuid>/.
"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import get_llm_provider
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.db.session import engine as configured_engine
from app.main import app
from app.modules.consistency.judge import conclude
from app.modules.consistency.models import ConsistencyReview, ConsistencyRun
from app.modules.consistency.router import get_service
from app.modules.consistency.schemas import Judgement
from app.modules.consistency.service import ConsistencyService
from app.modules.tlr.models import TLR_TABLES, TlrCandidate, TlrRun
from app.modules.tlr.preprocessing import preprocess_sync
from app.modules.tlr.schemas import ArtifactInput, DatasetInput, RunOptions
from app.modules.tlr.service import TlrService
from app.providers.llm.base import LLMProvider
from scripts.run_consistency import markdown


class FixtureLLM(LLMProvider):
    model = "synthetic-smoke-fixture"

    async def chat_json(self, *, user_payload, **kwargs):
        packet = user_payload["packet"]
        code = packet["evidence"][0]
        return dict(
            requirement_clarity="clear",
            evidence_sufficiency="sufficient",
            covers_whole_requirement=True,
            missing_context=[],
            clarification_questions=[],
            requested_artifact_ids=[],
            summary="Synthetic protocol fixture; not a semantic evaluation.",
            implementations=[
                dict(
                    parts=[
                        dict(
                            requirement_quote=packet["requirement"]["text"],
                            unit_id=(packet.get("requirement_units") or [{}])[0].get("id"),
                            implemented_behavior="Synthetic fixture",
                            outcomes=["implemented"],
                            reason="Transport/grounding smoke only.",
                        )
                    ],
                    citations=[dict(evidence_id=code["id"], quote=code["text"], start=0)],
                )
            ],
        )


async def main(live, partial=False):
    root = Path("artifacts/consistency/smoke") / str(uuid4())
    root.mkdir(parents=True)
    settings = get_settings()
    provider, model_info = FixtureLLM(), {"source": "synthetic_fixture"}
    if live:
        async with AsyncSessionLocal() as existing:
            provider, model_info = await ConsistencyService(
                existing, settings, get_llm_provider(settings)
            ).model("local")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(root / 'smoke.sqlite').absolute().as_posix()}"
    )
    metadata = MetaData()
    for table in [*TLR_TABLES, ConsistencyRun.__table__, ConsistencyReview.__table__]:
        table.to_metadata(metadata)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            dataset_input = DatasetInput(
                tenant_id="smoke",
                project_id="consistency-smoke",
                version="1",
                provenance={
                    "purpose": "synthetic runnable fixture",
                    "tlr_links": "manually supplied",
                },
                artifacts=[
                    ArtifactInput(
                        external_id="R1",
                        kind="requirement",
                        revision="1",
                        locator="requirements.md#R1",
                        content=(
                            "The register(name) function shall remove leading and trailing "
                            "whitespace and return the resulting name for nonblank input."
                        ),
                    ),
                    ArtifactInput(
                        external_id="R2",
                        kind="requirement",
                        revision="1",
                        locator="requirements.md#R2",
                        content=(
                            "The register(name) function shall raise ValueError "
                            "when the input contains only whitespace."
                        ),
                    ),
                    ArtifactInput(
                        external_id="api",
                        kind="code",
                        revision="1",
                        locator="api.py",
                        content=(
                            "from names import normalize\n\ndef register(name):\n"
                            "    return normalize(name)\n"
                        ),
                    ),
                    ArtifactInput(
                        external_id="names",
                        kind="code",
                        revision="1",
                        locator="names.py",
                        content=(
                            "def normalize(name):\n    name = name.strip()\n    if not name:\n"
                            '        raise ValueError("blank name")\n    return name\n'
                        ),
                    ),
                ],
            )
            tlr_service = TlrService(session, None, provider, settings)
            dataset = await tlr_service.import_dataset(dataset_input)
            artifacts = await tlr_service.repo.artifacts(dataset.id)
            requirement_structure = {
                a.external_id: {"nodes": [
                    {"id": a.external_id + ":root", "parent_id": None,
                     "kind": "requirement", "text": a.content, "start": 0, "end": len(a.content),
                     "children": [a.external_id + ":leaf"]},
                    {"id": a.external_id + ":leaf", "parent_id": a.external_id + ":root",
                     "kind": "unit", "text": a.content, "start": 0, "end": len(a.content),
                     "children": [], "categories": [], "conditions": []},
                ]}
                for a in artifacts if a.kind == "requirement"
            }
            tlr = TlrRun(
                tenant_id="smoke",
                project_id="consistency-smoke",
                dataset_id=dataset.id,
                status="completed",
                stage="completed_with_errors" if partial else "completed",
                config={"source_ids": ["R1", "R2"], "target_ids": ["api", "names"]},
                manifest={"fixture": True, "requirement_hierarchy": {
                    "requirements": requirement_structure, "categories": {"categories": []}
                }},
                counts={},
            )
            session.add(tlr)
            await session.flush()
            elements = []
            for artifact in artifacts:
                elements += list(
                    preprocess_sync(
                        artifact,
                        "source" if artifact.kind == "requirement" else "target",
                        tlr.id,
                        RunOptions(),
                    )
                )
            session.add_all(elements)
            await session.flush()
            for source in [e for e in elements if e.role == "source"]:
                for rank, target in enumerate([e for e in elements if e.role == "target"], 1):
                    session.add(
                        TlrCandidate(
                            run_id=tlr.id,
                            source_element_id=source.id,
                            target_element_id=target.id,
                            rank=rank,
                            similarity=1,
                            decision="pending" if partial and rank == 2 else "related",
                            evidence={"origin": "manual smoke fixture"},
                        )
                    )
            await session.commit()
            service = ConsistencyService(session, settings, provider)

            async def model(_tenant):
                return provider, model_info

            service.model = model
            app.dependency_overrides[get_service] = lambda: service
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://smoke"
            ) as client:
                scope = {"tenant_id": "smoke", "project_id": "consistency-smoke"}
                response = await client.post(
                    "/api/v1/consistency/runs", json={**scope, "tlr_run_id": tlr.id}
                )
                response.raise_for_status()
                run_id = response.json()["data"]["id"]
                response = await client.post(
                    f"/api/v1/consistency/runs/{run_id}/execute", params=scope
                )
                response.raise_for_status()
                report = response.json()["data"]
                assert report["status"] == "completed", report["results"]
                assert report["snapshot"]["tlr_partial"] is partial
                assert report["snapshot"]["requirement_structure"] == requirement_structure
                assert {e["role"] for e in report["snapshot"]["tlr_elements"]} == {"source", "target"}
                assert all(e["content"] and e["end"] > e["start"] for e in report["snapshot"]["tlr_elements"])
                assert all(link["source_element_id"] and link["target_end"] > link["target_start"] for link in report["snapshot"]["links"])
                assert bool(report["snapshot"]["tlr_pending_candidates"]) is partial
                for result in report["results"]["requirements"].values():
                    assert result["packet"]["matching_scope_incomplete"] is partial
                    if partial:
                        raw = await FixtureLLM().chat_json(user_payload={"packet": result["packet"]})
                        part = raw["implementations"][0]["parts"][0]
                        part.update(outcomes=["missing_functionality"], missing_behavior="unmatched behavior", scope_complete=True)
                        judged = conclude(result["packet"], Judgement.model_validate(raw))
                        assert "missing_functionality" not in judged["outcomes"]
                        assert "judgement_error" in judged["outcomes"]
                response = await client.get(f"/api/v1/consistency/runs/{run_id}", params=scope)
                assert response.json()["data"]["results"] == report["results"]
                review_payload = {
                    **scope,
                    "requirement_id": "R1",
                    "decision": "unresolved",
                    "reviewer": "smoke-fixture",
                    "method": "human_review",
                    "evidence": "Synthetic API transport check; no human approval claimed.",
                    "evidence_locator": "fixture://review",
                    "snapshot_digest": report["snapshot"]["digest"],
                }
                response = await client.post(
                    f"/api/v1/consistency/runs/{run_id}/reviews", json=review_payload
                )
                response.raise_for_status()
                report = response.json()["data"]
                assert report["reviews"][0]["decision"] == "unresolved"
                # A forged quote must never become a supported result.
                result = next(iter(report["results"]["requirements"].values()))
                raw = await FixtureLLM().chat_json(user_payload={"packet": result["packet"]})
                raw["implementations"][0]["citations"][0]["quote"] = (
                    "fabricated nonexisting implementation"
                )
                assert (
                    "judgement_error"
                    in conclude(result["packet"], Judgement.model_validate(raw))["outcomes"]
                )
                response = await client.get(
                    f"/api/v1/consistency/runs/{run_id}", params={**scope, "tenant_id": "other"}
                )
                assert response.status_code == 404
            (root / "dataset.json").write_text(
                dataset_input.model_dump_json(indent=2), encoding="utf-8"
            )
            (root / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (root / "report.md").write_text(markdown(report), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "mode": "live" if live else "synthetic",
                        "output": str(root),
                        "metrics": report["metrics"],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        app.dependency_overrides.pop(get_service, None)
        await engine.dispose()
        await configured_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--partial", action="store_true", help="Verify partially completed TLR reuse")
    args = parser.parse_args()
    asyncio.run(main(args.live, args.partial))
