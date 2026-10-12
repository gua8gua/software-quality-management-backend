"""Read-only business tools. No agent loop, SQL tool, or second TLR is created here."""

from __future__ import annotations

import json
from collections import Counter

import bm25s
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.artifacts.models import Artifact, ContentNode, ContentRelation, File, Project
from app.modules.traceability.models import (
    Candidate,
    RetrievalProjection,
    SemanticAnnotation,
    TlrRun,
    TraceLink,
    TraceUnit,
)
from app.modules.traceability.projections import heading_path, node_content, raw_projection
from app.modules.traceability.retrievers import ChineseTokenizer, DenseRetriever

MAX_PAGE = 100
MAX_TEXT = 8000


class InformationTools:
    """Small, deterministic tool results with stable IDs and explicit source labels."""

    def __init__(self, session: AsyncSession, project_id: str, tenant_id: str = "local"):
        self.session, self.project_id, self.tenant_id = session, project_id, tenant_id

    async def project(self) -> Project:
        row = await self.session.get(Project, self.project_id)
        if row is None or row.tenant_id != self.tenant_id:
            raise ValueError("project not found in tenant scope")
        return row

    async def artifact(self, artifact_id: str) -> Artifact:
        await self.project()
        row = await self.session.get(Artifact, artifact_id)
        if row is None or row.project_id != self.project_id:
            raise ValueError("artifact not found in project scope")
        return row

    async def node(self, node_id: str) -> ContentNode:
        row = await self.session.get(ContentNode, node_id)
        if row is None:
            raise ValueError("node not found")
        await self.artifact(row.artifact_id)
        return row

    async def run(self, run_id: str) -> TlrRun:
        await self.project()
        row = await self.session.get(TlrRun, run_id)
        if row is None or row.project_id != self.project_id or row.tenant_id != self.tenant_id:
            raise ValueError("TLR run not found in project scope")
        return row

    @staticmethod
    def _page(offset: int, limit: int) -> tuple[int, int]:
        if offset < 0 or not 1 <= limit <= MAX_PAGE:
            raise ValueError(f"offset must be nonnegative and limit 1..{MAX_PAGE}")
        return offset, limit

    @staticmethod
    def _node_summary(node: ContentNode) -> dict:
        return {
            "node_id": node.id, "artifact_id": node.artifact_id,
            "parent_id": node.parent_id, "node_type": node.node_type,
            "docling_type": node.original_type, "title": node.title,
            "reading_order": node.reading_order, "sibling_ordinal": node.sibling_ordinal,
            "provenance": node.provenance_json, "docling_ref": node.docling_ref,
        }

    async def _nodes(self, artifact_id: str) -> list[ContentNode]:
        await self.artifact(artifact_id)
        return list(await self.session.scalars(select(ContentNode).where(
            ContentNode.artifact_id == artifact_id
        ).order_by(ContentNode.reading_order, ContentNode.id)))

    async def _relations(self, artifact_id: str) -> list[ContentRelation]:
        return list(await self.session.scalars(select(ContentRelation).where(
            ContentRelation.artifact_id == artifact_id)))

    async def list_artifacts(self, *, offset: int = 0, limit: int = 30) -> dict:
        self._page(offset, limit)
        await self.project()
        rows = list(await self.session.scalars(select(Artifact).where(
            Artifact.project_id == self.project_id
        ).order_by(Artifact.created_at, Artifact.id).offset(offset).limit(limit + 1)))
        has_more = len(rows) > limit
        return {"source": "artifact_registry", "items": [
            {"artifact_id": a.id, "external_id": a.external_id,
             "engineering_role": a.engineering_role, "file_format": a.file_format,
             "revision": a.revision, "file_id": a.file_id}
            for a in rows[:limit]], "has_more": has_more,
                "next_offset": offset + limit if has_more else None}

    async def get_artifact(self, artifact_id: str) -> dict:
        a = await self.artifact(artifact_id)
        f = await self.session.get(File, a.file_id) if a.file_id else None
        return {"source": "artifact_registry", "artifact_id": a.id,
                "project_id": a.project_id, "external_id": a.external_id,
                "engineering_role": a.engineering_role, "modality": a.modality,
                "file_format": a.file_format, "language": a.language,
                "revision": a.revision, "folder_id": a.folder_id,
                "file": ({"file_id": f.id, "filename": f.filename,
                          "media_type": f.media_type, "sha256": f.sha256} if f else None)}

    async def artifact_relations(self, artifact_id: str, *, run_id: str | None = None,
                                 offset: int = 0, limit: int = 50) -> dict:
        self._page(offset, limit)
        await self.artifact(artifact_id)
        query = select(TraceLink).where(or_(
            TraceLink.source_artifact_id == artifact_id,
            TraceLink.target_artifact_id == artifact_id))
        if run_id:
            await self.run(run_id)
            query = query.where(TraceLink.run_id == run_id)
        rows = list(await self.session.scalars(query.order_by(TraceLink.id)
                                              .offset(offset).limit(limit + 1)))
        has_more = len(rows) > limit
        return {"source": "formal_tlr_derived_artifact_relations",
                "artifact_id": artifact_id, "run_id": run_id,
                "items": [{"link_id": link.id, "run_id": link.run_id,
                           "source_artifact_id": link.source_artifact_id,
                           "target_artifact_id": link.target_artifact_id,
                           "source_node_id": link.source_node_id,
                           "target_node_id": link.target_node_id,
                           "relation": link.relation} for link in rows[:limit]],
                "has_more": has_more, "next_offset": offset + limit if has_more else None}

    async def artifact_status(self, artifact_id: str, *, offset: int = 0,
                              limit: int = 50) -> dict:
        self._page(offset, limit)
        nodes = await self._nodes(artifact_id)
        counts = Counter(n.node_type for n in nodes)
        original = Counter(n.original_type for n in nodes)
        visual = [n.id for n in nodes if n.node_type == "picture"]
        formula = [n.id for n in nodes if n.original_type == "formula"]
        no_provenance = [n.id for n in nodes if n.node_type in {"paragraph", "table", "picture"}
                         and not n.provenance_json]
        has_more = any(len(items) > offset + limit for items in
                       (visual, formula, no_provenance))
        return {"source": "canonical_tree", "artifact_id": artifact_id,
                "node_counts": dict(counts), "docling_type_counts": dict(original),
                "unparsed_picture_node_ids": visual[offset:offset + limit],
                "formula_node_ids": formula[offset:offset + limit],
                "missing_provenance_node_ids": no_provenance[offset:offset + limit],
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None,
                "parse_failure_regions": "not_recorded_by_current_schema"}

    async def document_outline(self, artifact_id: str, *, offset: int = 0,
                               limit: int = 50, max_depth: int = 8) -> dict:
        self._page(offset, limit)
        if not 0 <= max_depth <= 32:
            raise ValueError("max_depth must be 0..32")
        nodes = await self._nodes(artifact_id)
        by_id = {n.id: n for n in nodes}
        def depth(n: ContentNode) -> int:
            seen, current = set(), n
            d = 0
            while current.parent_id and current.parent_id in by_id and current.id not in seen:
                seen.add(current.id)
                d += 1
                current = by_id[current.parent_id]
            return d
        structures = [(n, depth(n)) for n in nodes
                      if n.node_type in {"document", "section", "group"}
                      and depth(n) <= max_depth]
        page = structures[offset:offset + limit]
        has_more = offset + limit < len(structures)
        return {"source": "canonical_tree", "artifact_id": artifact_id,
                "items": [{**self._node_summary(n), "depth": d} for n, d in page if d <= max_depth],
                "total_structural_nodes": len(structures),
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None}

    async def subtree(self, node_id: str, *, offset: int = 0, limit: int = 50,
                      max_depth: int = 2) -> dict:
        self._page(offset, limit)
        if not 0 <= max_depth <= 8:
            raise ValueError("max_depth must be 0..8")
        root = await self.node(node_id)
        nodes = await self._nodes(root.artifact_id)
        children: dict[str | None, list[ContentNode]] = {}
        for n in nodes:
            children.setdefault(n.parent_id, []).append(n)
        queue = [(root, 0)]
        result = []
        while queue:
            current, depth = queue.pop(0)
            result.append((current, depth))
            if depth < max_depth:
                queue.extend((child, depth + 1) for child in children.get(current.id, []))
        page = result[offset:offset + limit]
        has_more = offset + limit < len(result)
        return {"source": "canonical_tree", "root_id": node_id,
                "items": [{**self._node_summary(n), "depth": d,
                           "text_preview": n.text[:300]} for n, d in page],
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None}

    async def tree_level(self, artifact_id: str, depth: int, *, section_id: str | None = None,
                         node_type: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        self._page(offset, limit)
        if not 0 <= depth <= 32:
            raise ValueError("depth must be 0..32")
        if section_id:
            root = await self.node(section_id)
            if root.artifact_id != artifact_id:
                raise ValueError("section outside artifact")
        else:
            root = None
        nodes = await self._nodes(artifact_id)
        by_id = {n.id: n for n in nodes}
        selected = []
        for n in nodes:
            d, current, seen = 0, n, set()
            while current.parent_id and current.parent_id in by_id and current.id not in seen:
                seen.add(current.id)
                current = by_id[current.parent_id]
                d += 1
                if root and current.id == root.id:
                    break
            if root and current.id != root.id:
                continue
            if root:
                cursor, relative = n, 0
                while cursor.id != root.id:
                    cursor = by_id[cursor.parent_id]
                    relative += 1
                d = relative
            if d == depth and (node_type is None or n.node_type == node_type):
                selected.append(n)
        has_more = offset + limit < len(selected)
        return {"source": "canonical_tree", "artifact_id": artifact_id,
                "items": [self._node_summary(n) for n in selected[offset:offset + limit]],
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None}

    async def node_path(self, node_id: str) -> dict:
        node = await self.node(node_id)
        nodes = {n.id: n for n in await self._nodes(node.artifact_id)}
        path, current, seen = [], node, set()
        while current and current.id not in seen:
            seen.add(current.id)
            path.append(self._node_summary(current))
            current = nodes.get(current.parent_id)
        return {"source": "canonical_tree", "node_id": node_id, "path": list(reversed(path))}

    async def node_detail(self, node_id: str, *, text_start: int = 0,
                          text_limit: int = 4000, include_structure: bool = False,
                          structure_start: int = 0,
                          structure_limit: int = MAX_TEXT,
                          metadata_start: int = 0,
                          metadata_limit: int = MAX_TEXT) -> dict:
        node = await self.node(node_id)
        if (text_start < 0 or not 1 <= text_limit <= MAX_TEXT
                or structure_start < 0 or not 1 <= structure_limit <= MAX_TEXT
                or metadata_start < 0 or not 1 <= metadata_limit <= MAX_TEXT):
            raise ValueError("invalid text range")
        nodes = {n.id: n for n in await self._nodes(node.artifact_id)}
        all_relations = await self._relations(node.artifact_id)
        relations = [r for r in all_relations if r.source_node_id == node.id
                     or r.target_node_id == node.id]
        content = node_content(node, nodes, all_relations) if node.node_type == "table" else node.text
        structure_payload = None
        structure_preview = None
        structure_total_chars = 0
        if include_structure:
            serialized = json.dumps(node.content_json, ensure_ascii=False, separators=(",", ":"))
            structure_total_chars = len(serialized)
            structure_preview = serialized[structure_start:structure_start + structure_limit]
            if structure_total_chars <= MAX_TEXT and structure_start == 0:
                structure_payload = node.content_json
        metadata_serialized = json.dumps(node.metadata_json, ensure_ascii=False, separators=(",", ":"))
        metadata_total_chars = len(metadata_serialized)
        metadata_preview = (metadata_serialized[metadata_start:metadata_start + metadata_limit]
                            if include_structure else None)
        return {"source": "canonical_tree", **self._node_summary(node),
                "heading_path": heading_path(node, nodes),
                "text": content[text_start:text_start + text_limit],
                "text_start": text_start, "text_total_chars": len(content),
                "has_more_text": text_start + text_limit < len(content),
                "text_next_start": text_start + text_limit
                if text_start + text_limit < len(content) else None,
                "hierarchy": {"docling_parent_ref": node.docling_parent_ref,
                              "docling_children_refs": node.docling_children_refs,
                              "docling_depth": node.docling_depth,
                              "heading_level": node.heading_level,
                              "status": node.hierarchy_status,
                              "source": node.hierarchy_source,
                              "content_layer": node.content_layer},
                "content_json": structure_payload,
                "content_json_preview": structure_preview,
                "content_json_start": structure_start if include_structure else None,
                "content_json_limit": structure_limit if include_structure else None,
                "content_json_total_chars": structure_total_chars if include_structure else None,
                "content_json_has_more": (structure_start + structure_limit < structure_total_chars)
                if include_structure else False,
                "content_json_next_start": structure_start + structure_limit
                if include_structure and structure_start + structure_limit < structure_total_chars else None,
                "metadata": node.metadata_json if include_structure and len(metadata_serialized) <= MAX_TEXT
                else None,
                # Large metadata is paged instead of silently omitted. The
                # structured value is materialized only when it fits; callers
                # can request the next contiguous metadata_start page.
                "metadata_preview": metadata_preview,
                "metadata_start": metadata_start if include_structure else None,
                "metadata_limit": metadata_limit if include_structure else None,
                "metadata_total_chars": metadata_total_chars if include_structure else None,
                "metadata_has_more": (metadata_start + metadata_limit < metadata_total_chars)
                if include_structure else False,
                "metadata_next_start": metadata_start + metadata_limit
                if include_structure and metadata_start + metadata_limit < metadata_total_chars else None,
                "relation_count_limited": len(relations)}

    async def read_range(self, artifact_id: str, *, start_order: int = 0,
                         offset: int = 0, limit: int = 30,
                         page_number: int | None = None) -> dict:
        self._page(offset, limit)
        if start_order < 0 or (page_number is not None and page_number < 1):
            raise ValueError("invalid reading order or page")
        nodes = await self._nodes(artifact_id)
        by_id = {n.id: n for n in nodes}
        relations = await self._relations(artifact_id)
        def on_page(n: ContentNode) -> bool:
            return page_number is None or any(
                isinstance(p, dict) and p.get("page_no") == page_number
                for p in (n.provenance_json or []))
        rows = [n for n in nodes if n.reading_order >= start_order
                and n.node_type in {"paragraph", "table", "picture"} and on_page(n)]
        selected = rows[offset:offset + limit]
        def excerpt(n: ContentNode) -> str:
            return node_content(n, by_id, relations) if n.node_type == "table" else n.text
        return {"source": "canonical_tree", "artifact_id": artifact_id,
                "items": [{**self._node_summary(n), "text": excerpt(n)[:MAX_TEXT],
                           "text_total_chars": len(excerpt(n)),
                           "has_more_text": len(excerpt(n)) > MAX_TEXT} for n in selected],
                "has_more": len(rows) > offset + limit,
                "next_offset": offset + limit if len(rows) > offset + limit else None}

    async def neighborhood(self, node_id: str, *, sibling_limit: int = 4,
                           child_limit: int = 20, sibling_offset: int = 0,
                           child_offset: int = 0) -> dict:
        if (not 0 <= sibling_limit <= 20 or not 0 <= child_limit <= MAX_PAGE
                or sibling_offset < 0 or child_offset < 0):
            raise ValueError("invalid neighbor range")
        node = await self.node(node_id)
        nodes = await self._nodes(node.artifact_id)
        siblings = [n for n in nodes if n.parent_id == node.parent_id and n.id != node.id]
        siblings.sort(key=lambda n: (abs(n.sibling_ordinal - node.sibling_ordinal), n.sibling_ordinal, n.id))
        children = [n for n in nodes if n.parent_id == node.id]
        children.sort(key=lambda n: (n.sibling_ordinal, n.reading_order, n.id))
        ordered = [n for n in nodes if n.node_type in {"paragraph", "table", "picture"}]
        positions = {n.id: i for i, n in enumerate(ordered)}
        position = positions.get(node.id)
        related = await self.node_relations(node_id, limit=MAX_PAGE)
        return {"source": "canonical_tree", "node_id": node_id,
                "parent": self._node_summary(next(n for n in nodes if n.id == node.parent_id))
                if node.parent_id and any(n.id == node.parent_id for n in nodes) else None,
                "children": [self._node_summary(n) for n in children[child_offset:child_offset + child_limit]],
                "children_total": len(children), "child_offset": child_offset,
                "children_has_more": child_offset + child_limit < len(children),
                "children_next_offset": child_offset + child_limit
                if child_limit > 0 and child_offset + child_limit < len(children) else None,
                "siblings": [self._node_summary(n) for n in siblings[sibling_offset:sibling_offset + sibling_limit]],
                "siblings_total": len(siblings), "sibling_offset": sibling_offset,
                "siblings_has_more": sibling_offset + sibling_limit < len(siblings),
                "siblings_next_offset": sibling_offset + sibling_limit
                if sibling_limit > 0 and sibling_offset + sibling_limit < len(siblings) else None,
                "previous": self._node_summary(ordered[position - 1]) if position and position > 0 else None,
                "next": self._node_summary(ordered[position + 1])
                if position is not None and position + 1 < len(ordered) else None,
                "relations": related["items"],
                "relations_has_more": related["has_more"],
                "relations_next_offset": related["next_offset"] if related["has_more"] else None}

    async def node_relations(self, node_id: str, *, relation_type: str | None = None,
                             offset: int = 0, limit: int = 50) -> dict:
        self._page(offset, limit)
        await self.node(node_id)
        query = select(ContentRelation).where(or_(
            ContentRelation.source_node_id == node_id, ContentRelation.target_node_id == node_id))
        if relation_type:
            query = query.where(ContentRelation.relation_type == relation_type)
        rows = list(await self.session.scalars(query.order_by(ContentRelation.id).offset(offset).limit(limit + 1)))
        return {"source": "canonical_relations", "node_id": node_id,
                "items": [{"relation_id": r.id, "source_node_id": r.source_node_id,
                           "target_node_id": r.target_node_id, "relation_type": r.relation_type,
                           "status": r.status, "origin": r.origin, "source_text": r.source_text,
                           "target_docling_ref": r.target_docling_ref, "metadata": r.metadata_json}
                          for r in rows[:limit]], "has_more": len(rows) > limit,
                "next_offset": offset + limit if len(rows) > limit else None}

    async def relation_between(self, left_node_id: str, right_node_id: str) -> dict:
        await self.node(left_node_id)
        await self.node(right_node_id)
        rows = list(await self.session.scalars(select(ContentRelation).where(or_(
            (ContentRelation.source_node_id == left_node_id)
            & (ContentRelation.target_node_id == right_node_id),
            (ContentRelation.source_node_id == right_node_id)
            & (ContentRelation.target_node_id == left_node_id),
        )).limit(MAX_PAGE)))
        return {"source": "canonical_relations", "left_node_id": left_node_id,
                "right_node_id": right_node_id,
                "items": [{"relation_id": r.id, "relation_type": r.relation_type,
                           "source_node_id": r.source_node_id,
                           "target_node_id": r.target_node_id,
                           "status": r.status, "origin": r.origin} for r in rows]}

    async def table_cells(self, node_id: str, *, offset: int = 0,
                          limit: int = 50) -> dict:
        self._page(offset, limit)
        node = await self.node(node_id)
        if node.node_type != "table":
            raise ValueError("node is not a table")
        raw = (node.content_json or {}).get("docling_item", {})
        data = raw.get("data", {})
        cells = data.get("table_cells", [])
        return {"source": "docling_structured_table", "node_id": node_id,
                "docling_ref": node.docling_ref, "provenance": node.provenance_json,
                "num_rows": data.get("num_rows"), "num_cols": data.get("num_cols"),
                "cells": cells[offset:offset + limit], "has_more": offset + limit < len(cells),
                "next_offset": offset + limit if offset + limit < len(cells) else None}

    async def annotations(self, run_id: str, *, node_id: str | None = None,
                          section_id: str | None = None, role: str | None = None,
                          failures_only: bool = False, offset: int = 0,
                          limit: int = 50) -> dict:
        self._page(offset, limit)
        await self.run(run_id)
        if node_id:
            await self.node(node_id)
        scope_ids: set[str] | None = None
        if section_id:
            section = await self.node(section_id)
            nodes = await self._nodes(section.artifact_id)
            by_parent: dict[str | None, list[str]] = {}
            for item in nodes:
                by_parent.setdefault(item.parent_id, []).append(item.id)
            scope_ids = set()
            pending = [section_id]
            while pending:
                current = pending.pop()
                if current not in scope_ids:
                    scope_ids.add(current)
                    pending.extend(by_parent.get(current, []))
        query = select(SemanticAnnotation).where(SemanticAnnotation.run_id == run_id)
        if node_id:
            query = query.where(SemanticAnnotation.node_id == node_id)
        if scope_ids is not None:
            query = query.where(SemanticAnnotation.node_id.in_(scope_ids))
        if role:
            query = query.where(SemanticAnnotation.role == role)
        if failures_only:
            query = query.where(SemanticAnnotation.synopsis_status == "failed")
        rows = list(await self.session.scalars(query.order_by(SemanticAnnotation.node_id)
                                              .offset(offset).limit(limit + 1)))
        return {"source": "model_derived_annotation", "run_id": run_id,
                "items": [{"annotation_id": a.id, "node_id": a.node_id,
                           "role": a.role, "synopsis": a.semantic_synopsis,
                           "status": a.synopsis_status, "error": a.synopsis_error,
                           "model": a.model, "provider": a.provider,
                           "prompt_version": a.prompt_version,
                           "generation_version": a.generation_version,
                           "legacy": a.prompt_version != "semantic-annotation-joint-v1"}
                           for a in rows[:limit]], "has_more": len(rows) > limit,
                 "next_offset": offset + limit if len(rows) > limit else None}

    async def tlr_results(self, run_id: str, node_id: str, *, offset: int = 0,
                          limit: int = 50) -> dict:
        self._page(offset, limit)
        await self.run(run_id)
        await self.node(node_id)
        units = list(await self.session.scalars(select(TraceUnit).where(
            TraceUnit.run_id == run_id, TraceUnit.node_id == node_id)))
        if not units:
            return {"source": "formal_tlr", "run_id": run_id, "node_id": node_id,
                    "items": [], "has_more": False, "next_offset": None}
        unit_ids = [u.id for u in units]
        all_units = {u.id: u for u in await self.session.scalars(select(TraceUnit).where(
            TraceUnit.run_id == run_id))}
        rows = list(await self.session.scalars(select(Candidate).where(
            Candidate.run_id == run_id,
            or_(Candidate.source_unit_id.in_(unit_ids), Candidate.target_unit_id.in_(unit_ids))
        ).order_by(Candidate.fused_rank, Candidate.id).offset(offset).limit(limit + 1)))
        return {"source": "formal_tlr", "run_id": run_id, "node_id": node_id,
                "items": [{"candidate_id": c.id,
                           "source_node_id": all_units[c.source_unit_id].node_id,
                           "target_node_id": all_units[c.target_unit_id].node_id,
                           "relation": c.final_relation, "fused_rank": c.fused_rank,
                           "dense_rank": c.dense_rank, "dense_score": c.dense_score,
                           "bm25_rank": c.bm25_rank, "bm25_score": c.bm25_score,
                           "rrf_score": c.rrf_score,
                           "retrieval_sources": c.retrieval_sources,
                           "execution_status": c.execution_status,
                           "validation_status": c.validation_status,
                           "evidence": c.evidence, "explanation": c.explanation,
                           "provider": c.validator_provider, "model": c.validator_model,
                           "model_version": c.validator_model_version,
                           "prompt_version": c.prompt_version,
                           "schema_version": c.schema_version,
                           "error_code": c.error_code}
                           for c in rows[:limit]], "has_more": len(rows) > limit,
                 "next_offset": offset + limit if len(rows) > limit else None}

    async def trace_links(self, run_id: str, node_id: str, *, offset: int = 0,
                          limit: int = 50) -> dict:
        self._page(offset, limit)
        await self.run(run_id)
        await self.node(node_id)
        rows = list(await self.session.scalars(select(TraceLink).where(
            TraceLink.run_id == run_id,
            or_(TraceLink.source_node_id == node_id, TraceLink.target_node_id == node_id)
        ).order_by(TraceLink.id).offset(offset).limit(limit + 1)))
        return {"source": "formal_tlr_trace_links", "run_id": run_id, "node_id": node_id,
                "items": [{"link_id": r.id, "source_artifact_id": r.source_artifact_id,
                           "target_artifact_id": r.target_artifact_id,
                           "source_node_id": r.source_node_id,
                           "target_node_id": r.target_node_id,
                           "candidate_id": r.candidate_id, "relation": r.relation,
                           "evidence": r.evidence, "explanation": r.explanation,
                           "model": r.validator_model, "prompt_version": r.prompt_version}
                           for r in rows[:limit]], "has_more": len(rows) > limit,
                 "next_offset": offset + limit if len(rows) > limit else None}

    async def mandatory_tlr_nodes(self, run_id: str, source_node_id: str,
                                  *, offset: int = 0, limit: int = 50,
                                  target_artifact_ids: list[str] | None = None,
                                  target_node_ids: list[str] | None = None) -> dict:
        self._page(offset, limit)
        run = await self.run(run_id)
        await self.node(source_node_id)
        units = list(await self.session.scalars(select(TraceUnit).where(
            TraceUnit.run_id == run_id, TraceUnit.node_id == source_node_id,
            TraceUnit.side == "source")))
        top_k = int(((run.config or {}).get("options") or {}).get("fine_top_k", 20))
        rows = list(await self.session.scalars(select(Candidate).where(
            Candidate.run_id == run_id,
            Candidate.source_unit_id.in_([u.id for u in units])))) if units else []
        target_query = select(TraceUnit).where(
            TraceUnit.run_id == run_id, TraceUnit.side == "target")
        if target_artifact_ids:
            target_query = target_query.where(TraceUnit.artifact_id.in_(target_artifact_ids))
        if target_node_ids:
            target_query = target_query.where(TraceUnit.node_id.in_(target_node_ids))
        targets = {u.id: u for u in await self.session.scalars(target_query)}
        selected = [c for c in rows if c.target_unit_id in targets and ((
                        c.final_relation in {"direct", "related"}
                        and c.execution_status == "succeeded"
                        and c.validation_status == "valid")
                    or (c.fused_rank is not None and c.fused_rank <= top_k))]
        selected.sort(key=lambda c: (c.fused_rank is None, c.fused_rank or 10**9, c.id))
        return {"source": "formal_tlr", "run_id": run_id,
                "source_node_id": source_node_id,
                "scope": {"target_artifact_ids": sorted(target_artifact_ids or
                                                          {u.artifact_id for u in targets.values()}),
                          "target_node_ids": sorted(target_node_ids or
                                                     {u.node_id for u in targets.values()}),
                          "source_node_id": source_node_id,
                          "source_use": "requirement interpretation only"},
                "definition": "validated direct/related candidates plus RRF seeds within run fine_top_k; failures remain visible",
                "items": [{"candidate_id": c.id, "target_unit_id": c.target_unit_id,
                           "node_id": targets[c.target_unit_id].node_id,
                           "artifact_id": targets[c.target_unit_id].artifact_id,
                           "fused_rank": c.fused_rank, "relation": c.final_relation,
                           "execution_status": c.execution_status,
                           "candidate_scope": "target_node_for_evidence"}
                          for c in selected[offset:offset + limit] if c.target_unit_id in targets],
                 "has_more": offset + limit < len(selected),
                 "next_offset": offset + limit if offset + limit < len(selected) else None}

    async def bm25_terms(self, node_id: str, *, run_id: str | None = None,
                         offset: int = 0, limit: int = 50) -> dict:
        self._page(offset, limit)
        node = await self.node(node_id)
        user_terms: list[str] = []
        if run_id:
            run = await self.run(run_id)
            user_terms = list(((run.config or {}).get("options") or {}).get(
                "tokenizer_user_terms", []))
            projection = await self.session.scalar(select(RetrievalProjection).where(
                RetrievalProjection.run_id == run_id, RetrievalProjection.node_id == node_id,
                RetrievalProjection.channel == "raw"))
            if projection is None:
                raise ValueError("raw projection absent from requested run")
            text, source = projection.text, "stored_raw_projection"
        else:
            if node.node_type == "table":
                nodes = {n.id: n for n in await self._nodes(node.artifact_id)}
                text = node_content(node, nodes, await self._relations(node.artifact_id))
            else:
                text = node.text
            source = "canonical_node_content_not_formal_bm25_projection"
        terms = ChineseTokenizer(user_terms).tokenize(text)
        counts = Counter(terms)
        items = sorted(counts.items(), key=lambda item: item[0])
        return {"source": source, "node_id": node_id, "run_id": run_id,
                "tokenizer": "jieba-py cut_for_search lowercase",
                "tokenizer_user_terms": user_terms,
                "items": [{"term": term, "term_frequency": count}
                          for term, count in items[offset:offset + limit]],
                "total_tokens": len(terms), "has_more": offset + limit < len(items),
                 "next_offset": offset + limit if offset + limit < len(items) else None,
                "idf": "not_exposed", "per_term_contribution": "not_exposed"}

    async def search(self, artifact_id: str, query: str, *, mode: str = "bm25",
                     node_type: str | None = None, section_id: str | None = None,
                     offset: int = 0, limit: int = 20) -> dict:
        self._page(offset, limit)
        if not query.strip() or len(query) > 2000:
            raise ValueError("query must contain 1..2000 characters")
        if mode not in {"bm25", "exact", "heading"}:
            raise ValueError("mode must be bm25, exact, or heading")
        nodes = await self._nodes(artifact_id)
        artifact = await self.artifact(artifact_id)
        all_nodes = {n.id: n for n in nodes}
        relations = await self._relations(artifact_id)
        if section_id:
            section = await self.node(section_id)
            if section.artifact_id != artifact_id:
                raise ValueError("section outside artifact")
            by_id = {n.id: n for n in nodes}
            def inside(n: ContentNode) -> bool:
                seen, current = set(), n
                while current and current.id not in seen:
                    if current.id == section_id:
                        return True
                    seen.add(current.id)
                    current = by_id.get(current.parent_id)
                return False
            nodes = [n for n in nodes if inside(n)]
        if node_type:
            nodes = [n for n in nodes if n.node_type == node_type]
        if mode == "heading":
            matches = [(n, None) for n in nodes if query.lower() in n.title.lower()]
        elif mode == "exact":
            matches = [(n, None) for n in nodes if query in node_content(n, all_nodes, relations)]
        else:
            nodes = [n for n in nodes if n.node_type in {"paragraph", "table"}]
            tokenizer = ChineseTokenizer()
            corpus = [tokenizer.tokenize(raw_projection(artifact, n, all_nodes, relations)[1])
                      for n in nodes]
            if not corpus:
                matches = []
            else:
                model = bm25s.BM25(method="lucene")
                model.index(corpus, show_progress=False)
                result = model.retrieve([tokenizer.tokenize(query)],
                                        k=min(offset + limit + 1, len(nodes)), show_progress=False)
                matches = [(nodes[int(i)], float(score)) for i, score in
                           zip(result.documents[0], result.scores[0], strict=True)
                           if float(score) > 0]
        page = matches[offset:offset + limit]
        return {"source": "investigation_search_not_tlr", "mode": mode,
                "artifact_id": artifact_id, "query_terms": ChineseTokenizer().tokenize(query),
                "items": [{**self._node_summary(n), "score": score,
                           "text_preview": n.text[:300]} for n, score in page],
                 "has_more": len(matches) > offset + limit,
                 "next_offset": offset + limit if len(matches) > offset + limit else None}

    async def similar_nodes_bm25(self, node_id: str, *, artifact_id: str | None = None,
                                  offset: int = 0, limit: int = 20) -> dict:
        self._page(offset, limit)
        source = await self.node(node_id)
        target_artifact = await self.artifact(artifact_id or source.artifact_id)
        source_nodes = {n.id: n for n in await self._nodes(source.artifact_id)}
        source_relations = await self._relations(source.artifact_id)
        source_artifact = await self.artifact(source.artifact_id)
        query = raw_projection(source_artifact, source, source_nodes, source_relations)[1]
        all_targets = await self._nodes(target_artifact.id)
        targets = [n for n in all_targets
                   if n.node_type in {"paragraph", "table"} and n.id != source.id]
        target_nodes = {n.id: n for n in all_targets}
        target_relations = await self._relations(target_artifact.id)
        if not targets:
            return {"source": "investigation_bm25_not_tlr", "items": [],
                    "has_more": False, "next_offset": None}
        tokenizer = ChineseTokenizer()
        corpus = [tokenizer.tokenize(raw_projection(target_artifact, n, target_nodes,
                                                    target_relations)[1]) for n in targets]
        model = bm25s.BM25(method="lucene")
        model.index(corpus, show_progress=False)
        result = model.retrieve([tokenizer.tokenize(query)],
                                k=min(offset + limit + 1, len(targets)), show_progress=False)
        matches = [(targets[int(i)], float(score)) for i, score in
                   zip(result.documents[0], result.scores[0], strict=True) if float(score) > 0]
        return {"source": "investigation_bm25_not_tlr", "query_node_id": node_id,
                "target_artifact_id": target_artifact.id,
                "items": [{**self._node_summary(n), "bm25_score": score}
                          for n, score in matches[offset:offset + limit]],
                "has_more": len(matches) > offset + limit,
                "next_offset": offset + limit if len(matches) > offset + limit else None}

    async def dense_search(self, run_id: str, query_vector: list[float], *,
                           offset: int = 0, limit: int = 20) -> dict:
        """Search only precomputed formal-TLR embeddings; never creates an index or embedding."""
        self._page(offset, limit)
        await self.run(run_id)
        units = list(await self.session.scalars(select(TraceUnit).where(
            TraceUnit.run_id == run_id, TraceUnit.side == "target")))
        projections = {p.id: p for p in await self.session.scalars(select(RetrievalProjection).where(
            RetrievalProjection.run_id == run_id, RetrievalProjection.channel == "raw"))}
        dimension = next((len(p.embedding) for p in projections.values() if p.embedding is not None), None)
        if dimension is None or len(query_vector) != dimension:
            raise ValueError("query vector dimension does not match existing raw projections")
        hits = await DenseRetriever(self.session).search_vector(
            query_vector, units, projections, top_k=min(offset + limit + 1, MAX_PAGE))
        return {"source": "existing_formal_tlr_embeddings", "run_id": run_id,
                "items": [{"node_id": unit.node_id, "rank": rank, "cosine_score": score}
                          for unit, rank, score in hits[offset:offset + limit]],
                 "has_more": len(hits) > offset + limit,
                 "next_offset": offset + limit if len(hits) > offset + limit else None,
                "scope_note": "Only nodes with existing raw TLR embeddings are searchable."}

    async def run_history(self, *, offset: int = 0, limit: int = 30) -> dict:
        self._page(offset, limit)
        await self.project()
        rows = list(await self.session.scalars(select(TlrRun).where(
            TlrRun.project_id == self.project_id, TlrRun.tenant_id == self.tenant_id
        ).order_by(TlrRun.created_at.desc(), TlrRun.id).offset(offset).limit(limit + 1)))
        return {"source": "formal_tlr_history", "items": [
            {"run_id": r.id, "status": r.status, "result_status": r.result_status,
             "stage": r.stage, "created_at": r.created_at.isoformat(),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "config": r.config, "metrics": r.metrics, "error": r.error}
            for r in rows[:limit]], "has_more": len(rows) > limit,
                "next_offset": offset + limit if len(rows) > limit else None}

    async def run_detail(self, run_id: str, *, failure_offset: int = 0,
                         failure_limit: int = 50) -> dict:
        self._page(failure_offset, failure_limit)
        run = await self.run(run_id)
        failures = list(await self.session.scalars(select(Candidate).where(
            Candidate.run_id == run_id, Candidate.execution_status == "failed"
        ).order_by(Candidate.id).offset(failure_offset).limit(failure_limit + 1)))
        annotation_versions = set(await self.session.scalars(
            select(SemanticAnnotation.prompt_version).where(
                SemanticAnnotation.run_id == run_id).distinct()))
        annotation_models = list(await self.session.execute(select(
            SemanticAnnotation.provider, SemanticAnnotation.model,
            SemanticAnnotation.model_version,
        ).where(SemanticAnnotation.run_id == run_id).distinct().limit(MAX_PAGE)))
        return {"source": "formal_tlr_history", "run_id": run.id,
                "status": run.status, "result_status": run.result_status,
                "stage": run.stage, "config": run.config, "metrics": run.metrics,
                "error": run.error, "created_at": run.created_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "annotation_prompt_versions": sorted(annotation_versions),
                "annotation_models": [{"provider": provider, "model": model,
                                       "model_version": version}
                                      for provider, model, version in annotation_models],
                "failed_candidates": [{"candidate_id": c.id,
                                       "error_category": c.error_category,
                                       "error_code": c.error_code,
                                       "last_error": c.last_error}
                                      for c in failures[:failure_limit]],
                "has_more_failures": len(failures) > failure_limit,
                "next_failure_offset": failure_offset + failure_limit
                if len(failures) > failure_limit else None}
