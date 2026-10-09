from dataclasses import asdict, dataclass

from app.modules.artifacts.models import Artifact, ContentNode, ContentRelation
from app.modules.traceability.models import TraceUnit
from app.modules.traceability.projections import heading_path, node_content
from app.modules.traceability.retrievers import HybridCandidate


@dataclass
class SupportingItem:
    node_id: str
    node_type: str
    heading_path: list[str]
    content: str
    reason: str
    relation_type: str
    origin_node_id: str
    status: str = "available"


@dataclass
class EvidencePackage:
    source_requirement: dict
    candidate_design_node: dict
    supporting_context: list[SupportingItem]

    def as_prompt(self) -> dict:
        return {
            "domain": "software engineering / software requirements and design",
            "source_artifact_role": "Software Requirements Specification",
            "target_artifact_role": "Software Design Specification",
            "source_requirement": self.source_requirement,
            "candidate_design_node": self.candidate_design_node,
            "supporting_context": [asdict(item) for item in self.supporting_context],
            "task_relation_definition": (
                "Determine whether the candidate design evidence directly realizes, constrains, "
                "supports, or operationalizes the source requirement."
            ),
        }


def _supporting_item(
    node: ContentNode,
    origin: ContentNode,
    reason: str,
    relation_type: str,
    nodes: dict[str, ContentNode],
    relations: list[ContentRelation],
) -> SupportingItem:
    content = (
        "[unparsed picture: visual evidence unavailable]"
        if node.node_type == "picture"
        else node_content(node, nodes, relations)
    )
    return SupportingItem(
        node_id=node.id,
        node_type=node.node_type,
        heading_path=heading_path(node, nodes),
        content=content,
        reason=reason,
        relation_type=relation_type,
        origin_node_id=origin.id,
        status="visual_content_unparsed" if node.node_type == "picture" else "available",
    )


def assemble_evidence(
    source: ContentNode,
    target: ContentNode,
    source_artifact: Artifact,
    target_artifact: Artifact,
    nodes: dict[str, ContentNode],
    relations: list[ContentRelation],
    *,
    sibling_window: int,
) -> EvidencePackage:
    del source_artifact, target_artifact
    support: list[SupportingItem] = []
    seen = {target.id}

    if target.parent_id and (parent := nodes.get(target.parent_id)) is not None:
        support.append(
            _supporting_item(
                parent, target, "nearest parent section/group", "parent", nodes, relations
            )
        )
        seen.add(parent.id)

    siblings = sorted(
        [item for item in nodes.values() if item.parent_id == target.parent_id],
        key=lambda item: item.sibling_ordinal,
    )
    position = next((index for index, item in enumerate(siblings) if item.id == target.id), 0)
    for sibling in (
        siblings[max(0, position - sibling_window) : position]
        + siblings[position + 1 : position + 1 + sibling_window]
    ):
        if sibling.id not in seen and sibling.node_type in {"paragraph", "table", "picture"}:
            support.append(
                _supporting_item(sibling, target, "direct sibling", "sibling", nodes, relations)
            )
            seen.add(sibling.id)

    for relation in relations:
        if relation.source_node_id == target.id:
            related_id = relation.target_node_id
            direction = "outgoing"
        elif relation.target_node_id == target.id:
            related_id = relation.source_node_id
            direction = "incoming"
        else:
            continue
        related = nodes.get(related_id) if related_id else None
        if related is None or related.id in seen:
            continue
        support.append(
            _supporting_item(
                related,
                target,
                f"{direction} canonical relation",
                relation.relation_type,
                nodes,
                relations,
            )
        )
        seen.add(related.id)

    return EvidencePackage(
        source_requirement={
            "node_id": source.id,
            "heading_path": heading_path(source, nodes),
            "original_content": node_content(source, nodes, relations),
        },
        candidate_design_node={
            "node_id": target.id,
            "node_type": target.node_type,
            "evidence_status": (
                "visual_content_unparsed" if target.node_type == "picture" else "available"
            ),
            "heading_path": heading_path(target, nodes),
            "original_content": node_content(target, nodes, relations),
        },
        supporting_context=support,
    )


def structural_expand(
    candidates: list[HybridCandidate],
    target_units: list[TraceUnit],
    nodes: dict[str, ContentNode],
    relations: list[ContentRelation],
    *,
    sibling_window: int,
) -> list[HybridCandidate]:
    unit_by_node = {unit.node_id: unit for unit in target_units}
    by_pair = {(item.source.id, item.target.id): item for item in candidates}
    base = list(candidates)
    for candidate in base:
        node = nodes[candidate.target.node_id]
        related: list[tuple[ContentNode, dict]] = []
        siblings = sorted(
            [item for item in nodes.values() if item.parent_id == node.parent_id],
            key=lambda item: item.sibling_ordinal,
        )
        position = next((index for index, item in enumerate(siblings) if item.id == node.id), 0)
        for sibling in (
            siblings[max(0, position - sibling_window) : position]
            + siblings[position + 1 : position + 1 + sibling_window]
        ):
            related.append(
                (
                    sibling,
                    {
                        "reason": "direct sibling of retrieved node",
                        "relation_type": "sibling",
                        "origin_node_id": node.id,
                    },
                )
            )
        for relation in relations:
            if relation.source_node_id == node.id and relation.target_node_id:
                other_id = relation.target_node_id
            elif relation.target_node_id == node.id:
                other_id = relation.source_node_id
            else:
                continue
            if other_id in nodes:
                related.append(
                    (
                        nodes[other_id],
                        {
                            "reason": "one-hop canonical relation",
                            "relation_type": relation.relation_type,
                            "origin_node_id": node.id,
                        },
                    )
                )
        for related_node, reason in related:
            unit = unit_by_node.get(related_node.id)
            if unit is None or unit.id == candidate.target.id:
                continue
            key = (candidate.source.id, unit.id)
            expanded = by_pair.get(key)
            if expanded is None:
                expanded = HybridCandidate(source=candidate.source, target=unit)
                expanded.retrieval_sources.add("structure")
                by_pair[key] = expanded
            if reason not in expanded.structural_reasons:
                expanded.structural_reasons.append(reason)
    return sorted(
        by_pair.values(),
        key=lambda item: (
            0 if item.fused_rank is not None else 1,
            item.fused_rank or 10**9,
            item.target.ordinal,
            item.target.id,
        ),
    )
