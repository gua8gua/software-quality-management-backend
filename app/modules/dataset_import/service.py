"""Reusable dataset registry, normalization and idempotent TLR persistence."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tlr.models import TlrArtifact, TlrDataset, TlrDatasetTraceLink, TlrProject


KIND_MAP = {
    "requirement": "requirement", "safetyrequirement": "requirement", "processrequirement": "requirement",
    "design": "design", "designdefinition": "design", "component": "component", "feature": "component",
    "task": "task", "code": "source_code", "sourcecode": "source_code", "acceptancetest": "acceptance_test",
    "test": "acceptance_test", "testcase": "acceptance_test", "hazard": "hazard",
    "environmentalassumption": "environmental_assumption", "context": "environmental_assumption",
}


@dataclass
class NormalizedArtifact:
    original_id: str
    original_type: str
    content: str | None
    locator: str
    source_locator: str
    structure: dict[str, Any] = field(default_factory=dict)
    content_mode: str = "inline"
    content_path: str | None = None


@dataclass
class NormalizedTrace:
    source: str
    target: str
    relation: str
    original: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedDataset:
    source_dataset: str
    source_version: str
    project_id: str
    project_name: str
    provenance: dict[str, Any]
    artifacts: list[NormalizedArtifact]
    traces: list[NormalizedTrace]


class DatasetAdapter(Protocol):
    name: str
    def load(self, path: Path) -> NormalizedDataset: ...


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def normalized_kind(value: str) -> str:
    key = "".join(ch for ch in value.lower() if ch.isalnum())
    return KIND_MAP.get(key, key or "unknown")


class DatasetImportService:
    def __init__(self):
        self.adapters: dict[str, DatasetAdapter] = {}

    def register(self, adapter: DatasetAdapter) -> None:
        self.adapters[adapter.name] = adapter

    async def import_file(self, session: AsyncSession, name: str, path: Path) -> dict[str, Any]:
        dataset = self.adapters[name].load(path)
        canonical = {"source": dataset.source_dataset, "version": dataset.source_version,
                     "artifacts": [a.__dict__ for a in dataset.artifacts], "traces": [t.__dict__ for t in dataset.traces]}
        digest = _digest(canonical)
        existing = await session.scalar(select(TlrDataset).where(
            TlrDataset.tenant_id == "local", TlrDataset.project_id == dataset.project_id,
            TlrDataset.version == dataset.source_version))
        if existing:
            if existing.digest != digest:
                raise ValueError(f"数据集版本已存在但内容不同: {dataset.source_dataset}/{dataset.source_version}")
            return {"status": "already_imported", "dataset_id": existing.id, "artifacts": 0, "traces": 0}
        project = await session.get(TlrProject, {"tenant_id": "local", "id": dataset.project_id})
        if not project:
            session.add(TlrProject(tenant_id="local", id=dataset.project_id, name=dataset.project_name,
                                   description="Dronology project-level dataset"))
            await session.flush()
        row = TlrDataset(tenant_id="local", project_id=dataset.project_id, version=dataset.source_version,
                         source_dataset=dataset.source_dataset,
                         digest=digest, provenance=dataset.provenance)
        session.add(row)
        await session.flush()
        ids: dict[str, str] = {}
        namespace = dataset.source_dataset + ":"
        for item in dataset.artifacts:
            external_id = namespace + item.original_id
            artifact = TlrArtifact(dataset_id=row.id, external_id=external_id,
                kind=normalized_kind(item.original_type), revision=dataset.source_version,
                locator=item.locator, content=item.content, content_mode=item.content_mode,
                content_path=item.content_path, sha256=hashlib.sha256((item.content or "").encode()).hexdigest(),
                structure={**item.structure, "source_dataset": dataset.source_dataset,
                           "source_version": dataset.source_version, "original_id": item.original_id,
                           "original_type": item.original_type, "source_locator": item.source_locator,
                           "provenance": dataset.provenance})
            session.add(artifact)
            await session.flush()
            ids[item.original_id] = artifact.id
        for trace in dataset.traces:
            if trace.source in ids and trace.target in ids:
                session.add(TlrDatasetTraceLink(dataset_id=row.id, source_artifact_id=ids[trace.source],
                    target_artifact_id=ids[trace.target], relation=trace.relation,
                    source_dataset=dataset.source_dataset, source_version=dataset.source_version,
                    source_type="dataset-provided", original_relation=trace.original,
                    provenance={"source_dataset": dataset.source_dataset, "source_version": dataset.source_version}))
        await session.commit()
        return {"status": "imported", "dataset_id": row.id, "artifacts": len(ids),
                "traces": sum(1 for t in dataset.traces if t.source in ids and t.target in ids),
                "unresolved_traces": sum(1 for t in dataset.traces if t.source not in ids or t.target not in ids)}
