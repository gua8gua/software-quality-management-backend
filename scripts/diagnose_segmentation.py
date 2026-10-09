"""Parse one PDF/DOCX and emit inspectable canonical-tree diagnostics."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.modules.parsing.service import parse_file
from app.modules.segmentation.service import LEAF_TYPES, segment


def _ref(value: Any) -> str:
    return str(getattr(value, "self_ref", "") or "")


def _raw_items(document: Any) -> list[Any]:
    result = []
    furniture = document.__dict__.get("furniture")
    for root in (document.body, furniture):
        if root is not None:
            result.extend(
                item
                for item, _ in document.iterate_items(
                    root=root,
                    with_groups=True,
                    traverse_pictures=True,
                )
            )
    return result


def _nested_texts(value: Any, result: dict[str, str]) -> None:
    if isinstance(value, dict):
        ref = value.get("self_ref")
        text = value.get("text")
        if isinstance(ref, str) and isinstance(text, str):
            result[ref] = text
        for child in value.values():
            _nested_texts(child, result)
    elif isinstance(value, list):
        for child in value:
            _nested_texts(child, result)


def _provenance_summary(node: Any, origin: dict[str, Any]) -> str:
    if node.provenance:
        parts = []
        for prov in node.provenance:
            parts.append(
                f"page={prov.get('page_no')}, bbox={prov.get('bbox')}, "
                f"charspan={prov.get('charspan')}"
            )
        return "; ".join(parts)
    filename = origin.get("filename", "") if origin else ""
    return f"origin={filename}, docling_ref={node.docling_ref or '-'}"


def diagnose(input_path: Path) -> tuple[dict[str, Any], str]:
    parsed = parse_file(input_path.name, input_path.read_bytes())
    result = segment(parsed)
    document = parsed.docling_document
    nodes = result.nodes
    relations = result.relations
    origin = nodes[0].content_data.get("origin") or {}

    preserved_by_ref: dict[str, str] = {}
    for node in nodes:
        if node.docling_ref:
            if node.node_type == "section":
                preserved_by_ref[node.docling_ref] = node.title
            elif node.node_type == "paragraph":
                preserved_by_ref[node.docling_ref] = node.text
        _nested_texts(node.content_data, preserved_by_ref)

    raw_texts = {
        _ref(item): str(getattr(item, "text", "") or "")
        for item in _raw_items(document)
        if _ref(item) and hasattr(item, "text") and str(getattr(item, "text", "") or "")
    }
    missing_text_refs = sorted(
        ref for ref, text in raw_texts.items() if preserved_by_ref.get(ref) != text
    )
    children: dict[int | None, list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        children[node.parent_index].append(index)

    attached_counts = Counter()
    reference_counts = Counter()
    for relation in relations:
        if relation.relation_type in {"caption_of", "footnote_of", "docling_reference_to"}:
            attached_counts[relation.source_index] += 1
            if relation.target_index is not None:
                attached_counts[relation.target_index] += 1
        if relation.relation_type == "explicit_reference":
            reference_counts[relation.source_index] += 1
            if relation.target_index is not None:
                reference_counts[relation.target_index] += 1

    tables = [node for node in nodes if node.node_type == "table"]
    pictures = [node for node in nodes if node.node_type == "picture"]
    table_cells = [
        cell
        for node in tables
        for cell in node.content_data.get("docling_item", {}).get("data", {}).get("table_cells", [])
    ]
    locatable = [
        node
        for node in nodes
        if node.node_type in LEAF_TYPES | {"section"}
        and (node.provenance or (node.docling_ref and origin))
    ]
    validation = {
        "raw_nonempty_text_items": len(raw_texts),
        "missing_text_count": len(missing_text_refs),
        "missing_text_refs": missing_text_refs,
        "canonical_types": dict(Counter(node.node_type for node in nodes)),
        "original_types": dict(Counter(node.original_type for node in nodes)),
        "hierarchy_status": dict(Counter(node.hierarchy_status for node in nodes)),
        "all_non_root_nodes_have_parent": all(node.parent_index is not None for node in nodes[1:]),
        "parents_precede_children": all(
            node.parent_index is not None and node.parent_index < index
            for index, node in enumerate(nodes[1:], start=1)
        ),
        "reading_order_is_contiguous": [node.reading_order for node in nodes]
        == list(range(len(nodes))),
        "locatable_leaf_or_heading_count": len(locatable),
        "native_provenance_count": sum(bool(node.provenance) for node in nodes),
        "origin": origin,
        "structured_table_count": sum(
            bool(node.content_data.get("docling_item", {}).get("data", {}).get("table_cells"))
            for node in tables
        ),
        "table_cell_count": len(table_cells),
        "table_header_cell_count": sum(
            bool(cell.get("column_header") or cell.get("row_header")) for cell in table_cells
        ),
        "table_spanned_cell_count": sum(
            int(cell.get("row_span", 1) or 1) > 1 or int(cell.get("col_span", 1) or 1) > 1
            for cell in table_cells
        ),
        "picture_count": len(pictures),
        "picture_image_object_count": sum(
            bool(node.content_data.get("docling_item", {}).get("image")) for node in pictures
        ),
        "picture_model_enrichment_enabled": False,
        "floating_leaf_has_canonical_children": any(
            nodes[index].node_type in {"table", "picture"} and children.get(index)
            for index in range(len(nodes))
        ),
        "section_nodes_with_copied_body_text": sum(
            bool(node.text) for node in nodes if node.node_type == "section"
        ),
        "semantic_unit_count": sum(node.node_type == "semantic_unit" for node in nodes),
        "leaf_over_30000_chars": sum(
            len(node.text) > 30_000 for node in nodes if node.node_type in LEAF_TYPES
        ),
        "retrievable_paragraph_or_table_count": sum(
            (node.node_type == "paragraph" and bool(node.text.strip()))
            or (
                node.node_type == "table"
                and bool(
                    node.content_data.get("docling_item", {}).get("data", {}).get("table_cells")
                )
            )
            for node in nodes
        ),
        "relations": {
            f"{kind}:{status}": count
            for (kind, status), count in Counter(
                (relation.relation_type, relation.status) for relation in relations
            ).items()
        },
        "docling_attachment_relation_count": sum(
            relation.origin == "docling" for relation in relations
        ),
        "unresolved_relation_count": sum(relation.status == "unresolved" for relation in relations),
    }

    node_json = []
    for index, node in enumerate(nodes):
        parent = nodes[node.parent_index] if node.parent_index is not None else None
        node_json.append(
            {
                "index": index,
                "canonical_type": node.node_type,
                "docling_original_type": node.original_type,
                "title": node.title,
                "text": node.text,
                "parent_index": node.parent_index,
                "parent_docling_ref": parent.docling_ref if parent else None,
                "sibling_ordinal": node.sibling_ordinal,
                "reading_order": node.reading_order,
                "docling_ref": node.docling_ref,
                "docling_parent_ref": node.docling_parent_ref,
                "docling_children_refs": node.docling_children_refs,
                "docling_depth": node.docling_depth,
                "heading_level": node.heading_level,
                "content_layer": node.content_layer,
                "hierarchy_status": node.hierarchy_status,
                "hierarchy_source": node.hierarchy_source,
                "provenance": node.provenance,
                "provenance_summary": _provenance_summary(node, origin),
                "attachment_relation_count": attached_counts[index],
                "reference_relation_count": reference_counts[index],
                "content_data": node.content_data,
                "metadata": node.metadata,
            }
        )

    relation_json = [
        {
            "relation_type": relation.relation_type,
            "source_index": relation.source_index,
            "target_index": relation.target_index,
            "status": relation.status,
            "origin": relation.origin,
            "source_text": relation.source_text,
            "target_docling_ref": relation.target_docling_ref,
            "metadata": relation.metadata,
        }
        for relation in relations
    ]

    lines = [
        f"# Canonical content tree: {input_path.name}",
        "",
        "## Validation",
        "",
        "```json",
        json.dumps(validation, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Tree",
        "",
    ]

    def emit(index: int, depth: int) -> None:
        node = nodes[index]
        label = node.title or node.text.replace("\n", " ")[:80]
        parent = nodes[node.parent_index].docling_ref if node.parent_index is not None else "-"
        lines.append(
            f"{'  ' * depth}- [{index}] {node.node_type} | raw={node.original_type} | "
            f"parent={parent or '-'} | sibling={node.sibling_ordinal} | "
            f"order={node.reading_order} | provenance={_provenance_summary(node, origin)} | "
            f"attachments={attached_counts[index]} | references={reference_counts[index]} | "
            f"{label}"
        )
        for child in children.get(index, []):
            emit(child, depth + 1)

    emit(0, 0)
    return (
        {
            "source": str(input_path),
            "metadata": result.metadata,
            "validation": validation,
            "nodes": node_json,
            "relations": relation_json,
        },
        "\n".join(lines) + "\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/segmentation"))
    args = parser.parse_args()
    payload, markdown = diagnose(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    json_path = args.output_dir / f"{stem}.canonical-tree.json"
    markdown_path = args.output_dir / f"{stem}.canonical-tree.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    print(json.dumps(payload["validation"], ensure_ascii=False, indent=2))
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
