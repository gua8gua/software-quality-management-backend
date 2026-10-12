from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from docling_core.types.doc import DoclingDocument, TableItem

from app.modules.artifacts.models import Artifact, ContentNode, ContentRelation


def document_type(artifact: Artifact) -> str:
    mapping = {"requirement": "SRS", "design": "SDS"}
    try:
        return mapping[artifact.engineering_role]
    except KeyError as exc:
        raise ValueError(
            f"artifact {artifact.external_id!r} must have engineering_role requirement or design"
        ) from exc


def heading_path(node: ContentNode, nodes: dict[str, ContentNode]) -> list[str]:
    result: list[str] = []
    current: ContentNode | None = node
    visited: set[str] = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        if current.node_type in {"document", "section"} and current.title.strip():
            result.append(current.title.strip())
        current = nodes.get(current.parent_id) if current.parent_id else None
    return list(reversed(result))


def _captions(
    node: ContentNode,
    nodes: dict[str, ContentNode],
    relations: list[ContentRelation],
) -> list[str]:
    return [
        nodes[item.source_node_id].text.strip()
        for item in relations
        if item.relation_type == "caption_of"
        and item.target_node_id == node.id
        and item.source_node_id in nodes
        and nodes[item.source_node_id].text.strip()
    ]


_DOCLING_COLLECTIONS = {
    "groups",
    "texts",
    "pictures",
    "tables",
    "key_value_items",
    "form_items",
    "field_regions",
    "field_items",
}
_DOCLING_ARRAY_REF = re.compile(r"^#/([^/]+)/(\d+)$")


def _rewrite_ref_value(value: Any, ref_map: dict[str, str]) -> Any:
    """Rewrite references inside one retained Docling item.

    The canonical tree stores a table plus the Docling descendants referenced
    by rich cells.  Their original array indexes are document-wide and are not
    contiguous, so a standalone serializer document must remap them.  External
    references (for example the original table parent or caption) are omitted;
    captions are projected from canonical ``ContentRelation`` records.
    """
    if isinstance(value, list):
        return [
            rewritten
            for item in value
            if (rewritten := _rewrite_ref_value(item, ref_map)) is not None
        ]
    if isinstance(value, dict):
        if set(value) == {"cref"}:
            mapped = ref_map.get(str(value["cref"]))
            return {"cref": mapped} if mapped is not None else None
        return {
            key: rewritten
            for key, item in value.items()
            if (rewritten := _rewrite_ref_value(item, ref_map)) is not None
        }
    return value


def _table_serialization_document(content: dict[str, Any]) -> tuple[DoclingDocument, TableItem]:
    """Rebuild the smallest valid Docling document for its official serializer."""
    raw_table = content["docling_item"]
    retained = [raw_table, *content.get("docling_subtree", [])]
    collections: dict[str, list[dict[str, Any]]] = {name: [] for name in _DOCLING_COLLECTIONS}
    ref_map: dict[str, str] = {}

    for raw_item in retained:
        original_ref = str(raw_item.get("self_ref", ""))
        match = _DOCLING_ARRAY_REF.fullmatch(original_ref)
        if match is None or match.group(1) not in collections:
            continue
        collection = match.group(1)
        if original_ref in ref_map:
            continue
        ref_map[original_ref] = f"#/{collection}/{len(collections[collection])}"
        collections[collection].append(raw_item)

    table_ref = str(raw_table.get("self_ref", ""))
    if table_ref not in ref_map:
        raise ValueError(f"invalid Docling table self_ref: {table_ref!r}")

    payload: dict[str, Any] = {"name": "retrieval_projection"}
    for collection, raw_items in collections.items():
        rewritten_items = []
        for source in raw_items:
            item = deepcopy(source)
            item["self_ref"] = ref_map[str(source["self_ref"])]
            for field in ("parent", "children", "captions", "footnotes", "references"):
                if field in item:
                    item[field] = _rewrite_ref_value(item[field], ref_map)
            if "data" in item:
                item["data"] = _rewrite_ref_value(item["data"], ref_map)
            rewritten_items.append(item)
        if rewritten_items:
            payload[collection] = rewritten_items

    document = DoclingDocument.model_validate(payload)
    mapped_table_ref = ref_map[table_ref]
    table_index = int(mapped_table_ref.rsplit("/", 1)[1])
    return document, document.tables[table_index]


def node_content(
    node: ContentNode,
    nodes: dict[str, ContentNode],
    relations: list[ContentRelation],
) -> str:
    if node.node_type == "paragraph":
        return node.text
    if node.node_type != "table":
        return ""
    parts: list[str] = []
    captions = _captions(node, nodes, relations)
    if captions:
        parts.append("Caption:\n" + "\n".join(captions))
    # Keep rich-cell references resolvable and let Docling's maintained HTML
    # serializer preserve headers, row/column structure and spans.
    serialization_doc, serialization_item = _table_serialization_document(node.content_json)
    parts.append(
        "Table:\n" + serialization_item.export_to_html(doc=serialization_doc, add_caption=False)
    )
    return "\n".join(parts)


def raw_projection(
    artifact: Artifact,
    node: ContentNode,
    nodes: dict[str, ContentNode],
    relations: list[ContentRelation],
) -> tuple[list[str], str]:
    path = heading_path(node, nodes)
    text = (
        f"Document type: {document_type(artifact)}\n"
        f"Section: {' > '.join(path)}\n"
        f"Content:\n{node_content(node, nodes, relations)}"
    )
    return path, text


def synopsis_projection(document_kind: str, path: list[str], synopsis: str) -> str:
    return (
        f"Document type: {document_kind}\n"
        f"Section: {' > '.join(path)}\n"
        f"Semantic synopsis:\n{synopsis}"
    )
