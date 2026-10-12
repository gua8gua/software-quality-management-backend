"""Measure real canonical projection lengths with official Qwen tokenizers."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from transformers import AutoTokenizer

from app.db.base import Base
from app.modules.artifacts.models import ContentNode, ContentRelation
from app.modules.artifacts.schemas import ArtifactCreate
from app.modules.traceability.projections import raw_projection
from app.modules.traceability.service import TraceabilityService
from app.providers.rerankers.qwen_transformers import QwenTransformersReranker


def _summary(values: list[int]) -> dict:
    if not values:
        return {"count": 0, "min": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0}
    array = np.asarray(values)
    return {
        "count": len(values),
        "min": int(array.min()),
        "p50": int(np.percentile(array, 50)),
        "p90": int(np.percentile(array, 90)),
        "p95": int(np.percentile(array, 95)),
        "p99": int(np.percentile(array, 99)),
        "max": int(array.max()),
    }


async def diagnose(srs_path: Path, sds_path: Path) -> dict:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        service = TraceabilityService(session)
        artifacts = []
        for path, role in ((srs_path, "requirement"), (sds_path, "design")):
            artifacts.append(
                await service.ingest_file(
                    "local",
                    "token-diagnostics",
                    path.name,
                    await asyncio.to_thread(path.read_bytes),
                    ArtifactCreate(external_id=path.stem, engineering_role=role),
                )
            )
        nodes = list(
            await session.scalars(
                select(ContentNode).where(
                    ContentNode.artifact_id.in_([artifact.id for artifact in artifacts])
                )
            )
        )
        relations = list(
            await session.scalars(
                select(ContentRelation).where(
                    ContentRelation.artifact_id.in_([artifact.id for artifact in artifacts])
                )
            )
        )
        node_map = {node.id: node for node in nodes}
        artifact_map = {artifact.id: artifact for artifact in artifacts}
        projections = [
            (node, raw_projection(artifact_map[node.artifact_id], node, node_map, relations)[1])
            for node in nodes
            if node.node_type in {"paragraph", "table"}
        ]

    embedding_model = "Qwen/Qwen3-Embedding-0.6B"
    reranker_model = "Qwen/Qwen3-Reranker-0.6B"
    embedding_tokenizer = AutoTokenizer.from_pretrained(embedding_model)
    reranker_tokenizer = AutoTokenizer.from_pretrained(reranker_model)
    embedding_lengths = [
        len(ids)
        for ids in embedding_tokenizer(
            [text for _, text in projections], add_special_tokens=True, truncation=False
        )["input_ids"]
    ]
    adapter = QwenTransformersReranker(reranker_model, max_length=8192)
    adapter._tokenizer = reranker_tokenizer
    reranker_tokenizer = adapter._load_tokenizer()
    pair_bodies = adapter._pairs("", [text for _, text in projections], "")
    overhead = len(adapter._prefix_tokens) + len(adapter._suffix_tokens)
    reranker_node_lengths = [
        len(ids) + overhead
        for ids in reranker_tokenizer(
            pair_bodies, add_special_tokens=False, truncation=False
        )["input_ids"]
    ]
    by_type_embedding: dict[str, list[int]] = defaultdict(list)
    by_type_reranker: dict[str, list[int]] = defaultdict(list)
    for (node, _), embedding_length, reranker_length in zip(
        projections, embedding_lengths, reranker_node_lengths, strict=True
    ):
        by_type_embedding[node.node_type].append(embedding_length)
        by_type_reranker[node.node_type].append(reranker_length)
    result = {
        "models": {
            "embedding": {
                "name": embedding_model,
                "tokenizer_revision": embedding_tokenizer.init_kwargs.get("_commit_hash"),
                "official_sequence_limit": 32768,
            },
            "reranker": {
                "name": reranker_model,
                "tokenizer_revision": reranker_tokenizer.init_kwargs.get("_commit_hash"),
                "configured_pair_limit": 8192,
            },
        },
        "embedding": {
            kind: _summary(lengths) for kind, lengths in sorted(by_type_embedding.items())
        },
        "reranker_node_only": {
            kind: _summary(lengths) for kind, lengths in sorted(by_type_reranker.items())
        },
        "embedding_oversize_nodes": sum(length > 32768 for length in embedding_lengths),
        "reranker_8192_node_only_oversize": sum(
            length > 8192 for length in reranker_node_lengths
        ),
        "note": (
            "Reranker node-only values include the official prefix/suffix and an empty query. "
            "Runtime preflight checks the exact query+document pair and refuses silent truncation."
        ),
    }
    await engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--srs", type=Path, required=True)
    parser.add_argument("--sds", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/traceability/token-lengths.json")
    )
    args = parser.parse_args()
    payload = asyncio.run(diagnose(args.srs.resolve(), args.sds.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
