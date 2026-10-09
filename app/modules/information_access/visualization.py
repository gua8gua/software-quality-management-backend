"""Read-only TLR display projection. No retrieval, model call or extra content storage."""

from collections import Counter

from sqlalchemy import select

from app.modules.artifacts.models import Artifact, ContentNode, File
from app.modules.information_access.service import InformationTools
from app.modules.traceability.models import Candidate, TraceLink, TraceUnit
from app.modules.traceability.schemas import TraceLinkView


async def tlr_visualization(reader: InformationTools, run_id: str) -> dict:
    run = await reader.run(run_id)
    units = list(await reader.session.scalars(select(TraceUnit).where(
        TraceUnit.run_id == run_id).order_by(TraceUnit.side, TraceUnit.ordinal, TraceUnit.id)))
    artifact_ids = set(u.artifact_id for u in units)
    # Runs with no units yet still expose their actual input documents.
    for side in ("source_artifact_ids", "target_artifact_ids"):
        artifact_ids.update((run.config or {}).get(side, []))
    artifacts = list(await reader.session.scalars(select(Artifact).where(
        Artifact.project_id == reader.project_id, Artifact.id.in_(artifact_ids))))
    allowed = {a.id for a in artifacts}
    nodes = list(await reader.session.scalars(select(ContentNode).where(
        ContentNode.artifact_id.in_(allowed)).order_by(ContentNode.reading_order, ContentNode.id)))
    by_id = {n.id: n for n in nodes}
    visible = {u.node_id for u in units if u.artifact_id in allowed}
    for node_id in list(visible):
        current = by_id.get(node_id)
        seen = set()
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            visible.add(current.parent_id)
            current = by_id.get(current.parent_id)
    files = {f.id: f for f in await reader.session.scalars(select(File).where(
        File.project_id == reader.project_id, File.id.in_([a.file_id for a in artifacts if a.file_id])))}
    links = list(await reader.session.scalars(select(TraceLink).where(
        TraceLink.run_id == run_id).order_by(TraceLink.id))) if run.status == "completed" else []
    execution = Counter(await reader.session.scalars(select(Candidate.execution_status).where(
        Candidate.run_id == run_id)))
    return {
        "source": "stored_tlr_visualization", "run_id": run_id,
        "artifacts": [{"artifact_id": a.id, "filename": files[a.file_id].filename
                       if a.file_id in files else a.external_id,
                       "engineering_role": a.engineering_role} for a in artifacts],
        "nodes": [{**reader._node_summary(n), "preview": (n.text or "")[:160]}
                  for n in nodes if n.id in visible],
        "units": [{"unit_id": u.id, "node_id": u.node_id, "artifact_id": u.artifact_id,
                   "side": u.side, "ordinal": u.ordinal, "role": u.semantic_role}
                  for u in units if u.artifact_id in allowed],
        "links": [TraceLinkView.model_validate(link).model_dump(mode="json") for link in links],
        "candidate_execution_counts": dict(execution),
    }


async def tlr_scope(reader: InformationTools, run_id: str) -> dict:
    """Return the selectable node scope for a completed TLR.

    A TLR creates units for every eligible canonical node, but the analysis
    page must not present the whole canonical tree as a selectable scope.  A
    node is selectable only when it participates in at least one persisted
    candidate (or formal link) for this run.  Structural ancestors are added
    solely so the UI can render a document tree and cascade a parent choice;
    they are marked ``selectable=False`` when they are not trace units.
    """
    run = await reader.run(run_id)
    configured = {
        "source": set((run.config or {}).get("source_artifact_ids", [])),
        "target": set((run.config or {}).get("target_artifact_ids", [])),
    }
    units = list(await reader.session.scalars(select(TraceUnit).where(
        TraceUnit.run_id == run_id)))
    unit_by_id = {u.id: u for u in units}
    used_unit_ids: set[str] = set()
    candidates = list(await reader.session.scalars(select(Candidate).where(
        Candidate.run_id == run_id)))
    for candidate in candidates:
        if candidate.source_unit_id in unit_by_id:
            used_unit_ids.add(candidate.source_unit_id)
        if candidate.target_unit_id in unit_by_id:
            used_unit_ids.add(candidate.target_unit_id)
    links = list(await reader.session.scalars(select(TraceLink).where(
        TraceLink.run_id == run_id))) if run.status == "completed" else []
    unit_by_node = {(u.side, u.node_id): u for u in units}
    for link in links:
        for side, node_id in (("source", link.source_node_id), ("target", link.target_node_id)):
            unit = unit_by_node.get((side, node_id))
            if unit is not None:
                used_unit_ids.add(unit.id)

    used_nodes = {unit_by_id[uid].node_id for uid in used_unit_ids}
    artifact_ids = configured["source"] | configured["target"]
    nodes = list(await reader.session.scalars(select(ContentNode).where(
        ContentNode.artifact_id.in_(artifact_ids)).order_by(
            ContentNode.artifact_id, ContentNode.reading_order, ContentNode.id))) if artifact_ids else []
    by_id = {node.id: node for node in nodes}
    visible = set(used_nodes)
    for node_id in list(used_nodes):
        current = by_id.get(node_id)
        seen: set[str] = set()
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            visible.add(current.parent_id)
            current = by_id.get(current.parent_id)

    artifacts = list(await reader.session.scalars(select(Artifact).where(
        Artifact.project_id == reader.project_id, Artifact.id.in_(artifact_ids)))) if artifact_ids else []
    file_ids = [artifact.file_id for artifact in artifacts if artifact.file_id]
    files = {f.id: f for f in await reader.session.scalars(select(File).where(
        File.project_id == reader.project_id, File.id.in_(file_ids)))} if file_ids else {}
    node_unit_keys = {(u.side, u.node_id) for u in units if u.id in used_unit_ids}
    items = []
    for node in nodes:
        if node.id not in visible:
            continue
        side = "source" if node.artifact_id in configured["source"] else "target"
        is_unit = (side, node.id) in node_unit_keys
        items.append({
            **reader._node_summary(node),
            "side": side,
            "selectable": is_unit,
            "used": is_unit,
            "preview": (node.text or node.title or "")[:160],
        })
    return {
        "source": "stored_tlr_selection_scope",
        "run_id": run_id,
        "artifacts": [{
            "artifact_id": artifact.id,
            "filename": files[artifact.file_id].filename if artifact.file_id in files else artifact.external_id,
            "engineering_role": artifact.engineering_role,
            "side": "source" if artifact.id in configured["source"] else "target",
            "used_node_count": sum(1 for item in items if item["artifact_id"] == artifact.id and item["selectable"]),
        } for artifact in artifacts],
        "nodes": items,
        "selected_source_node_ids": sorted(
            node_id for side, node_id in node_unit_keys if side == "source"),
        "selected_target_node_ids": sorted(
            node_id for side, node_id in node_unit_keys if side == "target"),
        "note": "Only nodes participating in persisted TLR candidates or links are selectable; ancestors are display-only cascade groups.",
    }
