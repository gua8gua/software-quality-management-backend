"""Reusable, fingerprinted source-context index; no model output or trace labels cached."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.modules.consistency.structure import (
    artifact_fingerprint,
    code_structure,
    digest,
    document_sections,
)
from app.modules.tlr.models import TlrHierarchyNode

VERSION = "artifact-context-v1"


def is_document(artifact):
    return artifact.kind not in {"code", "test_code", "package"}


def has_body(artifact):
    return (
        bool(artifact.content.strip())
        and (artifact.structure or {}).get("content_status") != "reference_only"
    )


class ArtifactContext:
    def __init__(self, artifacts, data, key, reused):
        self.artifacts = {a.external_id: a for a in artifacts}
        self.data, self.key, self.reused = data, key, reused

    @classmethod
    async def prepare(cls, session, artifacts, tenant, project, dataset, cache_dir):
        rows = list(
            await session.scalars(
                select(TlrHierarchyNode).where(TlrHierarchyNode.dataset_id == dataset)
            )
        )
        ids = {a.id: a.external_id for a in artifacts}
        hierarchy = [
            {
                "id": n.id,
                "parent_id": n.parent_id,
                "artifact_id": ids.get(n.artifact_id),
                "title": n.title,
                "node_type": n.node_type,
            }
            for n in rows
        ]
        signature = {
            "version": VERSION,
            "tenant": tenant,
            "project": project,
            "dataset": dataset,
            "artifacts": {a.external_id: artifact_fingerprint(a) for a in artifacts},
            "hierarchy": sorted(hierarchy, key=lambda n: n["id"]),
        }
        key = digest(json.dumps(signature, ensure_ascii=False, sort_keys=True))

        def read_or_build():
            path = Path(cache_dir) / (key + ".json")
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                payload = cached["data"]
                if cached["key"] == key and cached["digest"] == digest(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True)
                ):
                    return payload, True
            except (OSError, ValueError, KeyError, TypeError):
                pass
            by_node = {n["id"]: n for n in hierarchy}
            data = {}
            for artifact in artifacts:
                parents, titles = [], []
                for node in hierarchy:
                    if node["artifact_id"] != artifact.external_id:
                        continue
                    seen, current = set(), by_node.get(node["parent_id"])
                    while current and current["id"] not in seen:
                        seen.add(current["id"])
                        titles.append(current["title"])
                        if current["artifact_id"]:
                            parents.append(current["artifact_id"])
                        current = by_node.get(current["parent_id"])
                metadata = artifact.structure or {}
                parents.extend(p for p in metadata.get("parent_ids", []) if isinstance(p, str))
                # Only explicit documentary references, never imported gold/trace relations.
                refs = [r for r in metadata.get("references", []) if isinstance(r, str)]
                data[artifact.external_id] = {
                    "fingerprint": signature["artifacts"][artifact.external_id],
                    "sections": document_sections(artifact),
                    "code": code_structure(artifact) if not is_document(artifact) else None,
                    "parents": list(dict.fromkeys(parents)),
                    "ancestor_titles": titles,
                    "references": list(dict.fromkeys(refs)),
                }
            envelope = {
                "key": key,
                "signature": signature,
                "data": data,
                "digest": digest(json.dumps(data, ensure_ascii=False, sort_keys=True)),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix("." + uuid4().hex + ".tmp")
            temp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
            return data, False

        data, reused = await asyncio.to_thread(read_or_build)
        return cls(artifacts, data, key, reused)

    def related_ids(self, identifier):
        item = self.data[identifier]
        return list(dict.fromkeys(item["parents"] + item["references"]))

    def around(self, identifier, start, end, budget=6000):
        artifact = self.artifacts[identifier]
        data = self.data[identifier]
        pieces, omitted = [], []
        used = 0

        def add(a, left, right, role):
            nonlocal used
            if left >= right:
                return
            if used + right - left > budget:
                omitted.append(
                    {
                        "artifact_id": a.external_id,
                        "start": left,
                        "end": right,
                        "reason": "context_budget",
                        "role": role,
                    }
                )
                return
            pieces.append(
                {
                    "artifact_id": a.external_id,
                    "start": left,
                    "end": right,
                    "text": a.content[left:right],
                    "role": role,
                }
            )
            used += right - left

        add(artifact, max(0, start - 800), start, "neighbor_before")
        add(artifact, end, min(len(artifact.content), end + 800), "neighbor_after")
        nodes = (data["code"] or {}).get("nodes", []) if data["code"] else data["sections"]
        containing = sorted(
            (n for n in nodes if n["start"] <= start and end <= n["end"]),
            key=lambda n: n["end"] - n["start"],
        )
        for node in containing[:2]:
            if (node["start"], node["end"]) != (start, end):
                add(artifact, node["start"], node["end"], "enclosing")
        for identifier in self.related_ids(artifact.external_id):
            parent = self.artifacts.get(identifier)
            if parent and has_body(parent):
                add(parent, 0, len(parent.content), "parent_or_reference")
            else:
                omitted.append({"artifact_id": identifier, "reason": "reference_body_unavailable"})
        return {
            "cache_key": self.key,
            "ancestor_titles": data["ancestor_titles"],
            "sections": [
                {"title": n.get("title", n.get("name", "")), "start": n["start"], "end": n["end"]}
                for n in containing
            ],
            "excerpts": pieces,
            "omitted": omitted,
        }

    def manifest(self):
        return {
            "version": VERSION,
            "key": self.key,
            "reused": self.reused,
            "artifacts": len(self.data),
            "scope": "tenant_project_dataset_fingerprints",
        }
