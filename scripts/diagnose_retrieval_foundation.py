"""Validate canonical projections and BM25 on real SRS/SDS without deploying ML models."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

import bm25s
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.modules.artifacts.models import ContentNode, ContentRelation
from app.modules.artifacts.schemas import ArtifactCreate
from app.modules.traceability.projections import raw_projection
from app.modules.traceability.retrievers import ChineseTokenizer
from app.modules.traceability.service import TraceabilityService


async def diagnose(srs_path: Path, sds_path: Path) -> dict:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        service = TraceabilityService(session)
        srs = await service.ingest_file(
            "local",
            "real-doc-diagnostics",
            srs_path.name,
            await asyncio.to_thread(srs_path.read_bytes),
            ArtifactCreate(external_id="real-srs", engineering_role="requirement"),
        )
        sds = await service.ingest_file(
            "local",
            "real-doc-diagnostics",
            sds_path.name,
            await asyncio.to_thread(sds_path.read_bytes),
            ArtifactCreate(external_id="real-sds", engineering_role="design"),
        )
        nodes = list(
            await session.scalars(
                select(ContentNode).where(ContentNode.artifact_id.in_([srs.id, sds.id]))
            )
        )
        relations = list(
            await session.scalars(
                select(ContentRelation).where(ContentRelation.artifact_id.in_([srs.id, sds.id]))
            )
        )
        node_map = {item.id: item for item in nodes}
        projections = []
        serialization_errors = []
        for node in nodes:
            if node.node_type not in {"paragraph", "table"}:
                continue
            artifact = srs if node.artifact_id == srs.id else sds
            try:
                path, text = raw_projection(artifact, node, node_map, relations)
                projections.append((node, path, text))
            except Exception as exc:  # diagnostic must report every corrupt table
                serialization_errors.append(
                    {"node_id": node.id, "node_type": node.node_type, "error": str(exc)}
                )

        sds_projections = [item for item in projections if item[0].artifact_id == sds.id]
        tokenizer = ChineseTokenizer(["MQTT", "Redis", "故障检测"])
        corpus = [tokenizer.tokenize(item[2]) for item in sds_projections]
        model = bm25s.BM25(method="lucene")
        model.index(corpus, show_progress=False)
        term_results = {}
        for term in ("模块名称", "MQTT", "Redis", "数据库", "故障检测"):
            result = model.retrieve(
                [tokenizer.tokenize(term)], k=min(5, len(corpus)), show_progress=False
            )
            term_results[term] = [
                {
                    "rank": rank,
                    "score": float(score),
                    "node_id": sds_projections[int(index)][0].id,
                    "heading_path": sds_projections[int(index)][1],
                    "node_type": sds_projections[int(index)][0].node_type,
                    "preview": sds_projections[int(index)][2][:300],
                }
                for rank, (index, score) in enumerate(
                    zip(result.documents[0], result.scores[0], strict=True), start=1
                )
                if float(score) > 0
            ]

        payload = {
            "documents": {"SRS": str(srs_path), "SDS": str(sds_path)},
            "canonical_counts": {
                "SRS": dict(
                    Counter(item.node_type for item in nodes if item.artifact_id == srs.id)
                ),
                "SDS": dict(
                    Counter(item.node_type for item in nodes if item.artifact_id == sds.id)
                ),
            },
            "projection_counts": {
                "SRS": sum(item[0].artifact_id == srs.id for item in projections),
                "SDS": len(sds_projections),
                "tables": sum(item[0].node_type == "table" for item in projections),
            },
            "table_serialization_errors": serialization_errors,
            "picture_count": sum(item.node_type == "picture" for item in nodes),
            "picture_projection_count": 0,
            "bm25_terms": term_results,
            "model_stages_executed": False,
            "model_stage_skip_reason": (
                "No local Qwen3 embedding/reranker deployment or configured LLM; model-quality "
                "testing was explicitly deferred."
            ),
        }
    await engine.dispose()
    return payload


def markdown(payload: dict) -> str:
    lines = ["# Real SRS/SDS retrieval-foundation diagnostics", ""]
    lines.append("## Counts")
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "canonical_counts",
                    "projection_counts",
                    "picture_count",
                    "picture_projection_count",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lines.append("```")
    lines.append("")
    lines.append("## BM25 term retrieval")
    lines.append("")
    for term, rows in payload["bm25_terms"].items():
        lines.append(f"### {term}")
        lines.append("")
        for row in rows:
            path = " > ".join(row["heading_path"])
            lines.append(
                f"- rank={row['rank']} score={row['score']:.6f} `{row['node_type']}` {path}"
            )
        lines.append("")
    lines.append("## Deferred model stages")
    lines.append("")
    lines.append(payload["model_stage_skip_reason"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--srs", type=Path, required=True)
    parser.add_argument("--sds", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/traceability"))
    args = parser.parse_args()
    payload = asyncio.run(diagnose(args.srs.resolve(), args.sds.resolve()))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "real-srs-sds-foundation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "real-srs-sds-foundation.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["projection_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
