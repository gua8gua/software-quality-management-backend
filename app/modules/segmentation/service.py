"""Normalize parsed input into the one canonical content tree.

For PDF/DOCX, Docling remains the source of truth.  This module maps Docling's
native items to six project types: document, section, group, paragraph, table,
and picture.  It never creates model-sized chunks or semantic-unit copies.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.modules.parsing.models import (
    ContentNodeDraft,
    ContentRelationDraft,
    ParsedContent,
    SegmentedContent,
)

LEAF_TYPES = {"paragraph", "table", "picture"}
FLOAT_TYPES = {"table", "picture"}
SECTION_LABELS = {"section_header", "title"}
GROUP_SECTION_LABELS = {"section", "chapter"}

_NUMBER = r"[A-Za-z]?\d+(?:[-.]\d+)*"
_CAPTION_RE = re.compile(rf"^\s*(?P<kind>[图表])\s*(?P<number>{_NUMBER})\b", re.IGNORECASE)
_SECTION_REF_RE = re.compile(
    r"(?:参见|详见|见|参考)?\s*第?\s*(?P<number>\d+(?:\.\d+)+)\s*(?:节|章)"
)
_FLOAT_REF_RE = re.compile(
    rf"(?:(?:参见|详见|见|参考|如下|如)\s*|(?<![\u4e00-\u9fff]))"
    rf"(?P<kind>[图表])\s*(?P<number>{_NUMBER})",
    re.IGNORECASE,
)
_SECTION_NUMBER_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)*)\b")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower().replace("-", "_")


def _ref(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return getattr(value, "cref", None) or getattr(value, "self_ref", None)


def _refs(values: Any) -> list[str]:
    return [result for value in (values or []) if (result := _ref(value))]


def _dump(value: Any, *, exclude: set[str] | None = None) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True, exclude=exclude or set())
    if isinstance(value, dict):
        return value
    return str(value)


def _provenance(item: Any) -> list[dict[str, Any]]:
    return [_dump(value) for value in (getattr(item, "prov", None) or [])]


def _text_payload(item: Any) -> dict[str, Any]:
    dumped = _dump(item)
    wanted = {"orig", "formatting", "hyperlink", "source", "comments", "meta"}
    return {key: dumped[key] for key in wanted if key in dumped}


def _item_ref(item: Any) -> str:
    return str(getattr(item, "self_ref", "") or "")


def _furniture(document: Any) -> Any:
    # docling-core 2.96 keeps this legacy root for round-trip compatibility but
    # marks attribute access deprecated; use the stored model value directly.
    return document.__dict__.get("furniture")


def _parent_ref(item: Any) -> str | None:
    return _ref(getattr(item, "parent", None))


def _iter_docling(document: Any) -> list[tuple[Any, int]]:
    entries: list[tuple[Any, int]] = []
    for root in (document.body, _furniture(document)):
        if root is None:
            continue
        entries.extend(
            document.iterate_items(
                root=root,
                with_groups=True,
                traverse_pictures=True,
            )
        )
    return entries


def _section_group_chain(
    item: Any, items: dict[str, Any], merged_groups: set[str]
) -> list[dict[str, Any]]:
    result = []
    parent_ref = _parent_ref(item)
    while parent_ref and parent_ref in items:
        parent = items[parent_ref]
        label = _enum_value(getattr(parent, "label", ""))
        if label not in GROUP_SECTION_LABELS or parent_ref not in merged_groups:
            break
        result.append(
            {
                "ref": parent_ref,
                "parent_ref": _parent_ref(parent),
                "children_refs": _refs(getattr(parent, "children", None)),
                "name": str(getattr(parent, "name", "") or ""),
                "label": label,
            }
        )
        parent_ref = _parent_ref(parent)
    return result


def _page_index(document: Any) -> dict[str, Any]:
    pages = {}
    for page_no, page in (getattr(document, "pages", None) or {}).items():
        pages[str(page_no)] = {
            "page_no": page_no,
            "size": _dump(getattr(page, "size", None)),
        }
    return pages


def _finalize_ordinals(nodes: list[ContentNodeDraft]) -> None:
    siblings: dict[int | None, list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        node.reading_order = index
        siblings[node.parent_index].append(index)
    for indexes in siblings.values():
        for ordinal, index in enumerate(indexes):
            nodes[index].sibling_ordinal = ordinal


def _normalize_docling(parsed: ParsedContent) -> SegmentedContent:
    document = parsed.docling_document
    entries = _iter_docling(document)
    items = {_item_ref(item): item for item, _ in entries if _item_ref(item)}

    def contains_heading(ref: str, seen: set[str] | None = None) -> bool:
        seen = seen or set()
        if ref in seen or ref not in items:
            return False
        seen.add(ref)
        for child_ref in _refs(getattr(items[ref], "children", None)):
            child = items.get(child_ref)
            if child is None:
                continue
            child_label = _enum_value(getattr(child, "label", ""))
            if child_label in SECTION_LABELS:
                return True
            if child_label in GROUP_SECTION_LABELS and contains_heading(child_ref, seen):
                return True
        return False

    merged_section_groups = {
        ref
        for ref, item in items.items()
        if _enum_value(getattr(item, "label", "")) in GROUP_SECTION_LABELS and contains_heading(ref)
    }

    body_ref = _item_ref(document.body) or "#/body"
    furniture = _furniture(document)
    root_data = {
        "origin": _dump(getattr(document, "origin", None)),
        "pages": _page_index(document),
        "docling_roots": {
            "body": {
                "ref": body_ref,
                "children_refs": _refs(getattr(document.body, "children", None)),
            },
            "furniture": {
                "ref": _item_ref(furniture),
                "children_refs": _refs(getattr(furniture, "children", None)),
            },
        },
        "docling_schema_name": str(getattr(document, "schema_name", "")),
        "docling_document_version": str(getattr(document, "version", "")),
    }
    nodes = [
        ContentNodeDraft(
            node_type="document",
            original_type="document",
            title=str(getattr(document, "name", "") or "document"),
            docling_ref=body_ref,
            docling_children_refs=_refs(getattr(document.body, "children", None)),
            docling_depth=0,
            content_layer="body",
            hierarchy_source="docling",
            content_data=root_data,
            metadata={"parser": parsed.metadata},
        )
    ]
    canonical_by_ref: dict[str, int] = {body_ref: 0}
    furniture_ref = _item_ref(_furniture(document))
    if furniture_ref:
        canonical_by_ref[furniture_ref] = 0

    attached_to_float: dict[str, tuple[str, str]] = {}
    for item, _ in entries:
        owner_ref = _item_ref(item)
        label = _enum_value(getattr(item, "label", ""))
        if label not in {"table", "document_index", "picture", "chart"}:
            continue
        for relation_type, attribute in (
            ("caption_of", "captions"),
            ("footnote_of", "footnotes"),
            ("docling_reference_to", "references"),
        ):
            for related_ref in _refs(getattr(item, attribute, None)):
                attached_to_float[related_ref] = (owner_ref, relation_type)

    def floating_ancestor(ref: str) -> str | None:
        current = items.get(ref)
        while current is not None and (parent_ref := _parent_ref(current)):
            parent = items.get(parent_ref)
            if parent is None:
                return None
            label = _enum_value(getattr(parent, "label", ""))
            if label in {"table", "document_index", "picture", "chart"}:
                return parent_ref
            current = parent
        return None

    suppressed_subtrees: dict[str, list[dict[str, Any]]] = defaultdict(list)
    section_stack: list[tuple[int, int]] = []

    def nearest_mapped_parent(item: Any, *, attach_to_active_section: bool) -> int:
        """Resolve Docling's structural parent into the canonical tree.

        DOCX conversion commonly parents every top-level block to ``#/body``
        even when headings establish a reading-order section hierarchy.  The
        old implementation returned the canonical document as soon as it saw
        that body ref, so the section-stack fallback was unreachable.  Keep
        real Docling groups, but attach body-level content to the active
        heading for body content.  Furniture never inherits the body stack.
        """
        parent_ref = _parent_ref(item)
        while parent_ref:
            if parent_ref in canonical_by_ref:
                parent_index = canonical_by_ref[parent_ref]
                if nodes[parent_index].node_type not in FLOAT_TYPES:
                    content_layer = (
                        _enum_value(getattr(item, "content_layer", "body")) or "body"
                    )
                    if (
                        attach_to_active_section
                        and parent_index == 0
                        and section_stack
                        and content_layer != "furniture"
                    ):
                        return section_stack[-1][1]
                    return parent_index
                return nodes[parent_index].parent_index or 0
            parent = items.get(parent_ref)
            parent_ref = _parent_ref(parent) if parent is not None else None
        if attach_to_active_section and section_stack:
            content_layer = _enum_value(getattr(item, "content_layer", "body")) or "body"
            if content_layer != "furniture":
                return section_stack[-1][1]
        return 0

    for item, depth in entries:
        ref = _item_ref(item)
        if not ref or ref in {body_ref, furniture_ref}:
            continue
        label = _enum_value(getattr(item, "label", ""))

        if ref in merged_section_groups:
            # Docling uses section groups as scaffolding around heading items.
            # The canonical section is the heading itself; the skipped group is
            # retained verbatim in that section's hierarchy metadata.
            continue

        ancestor = floating_ancestor(ref)
        if ancestor and ref not in attached_to_float:
            suppressed_subtrees[ancestor].append(_dump(item))
            continue

        parent_ref = _parent_ref(item)
        children_refs = _refs(getattr(item, "children", None))
        provenance = _provenance(item)
        content_layer = _enum_value(getattr(item, "content_layer", "body")) or "body"

        if label in SECTION_LABELS:
            heading_level = int(getattr(item, "level", 1) or 1) if label == "section_header" else 1
            while section_stack and section_stack[-1][0] >= heading_level:
                section_stack.pop()
            parent_index = section_stack[-1][1] if section_stack else 0
            raw_parent_index = nearest_mapped_parent(item, attach_to_active_section=False)
            corrected = raw_parent_index != parent_index
            group_chain = _section_group_chain(item, items, merged_section_groups)
            node = ContentNodeDraft(
                node_type="section",
                original_type=label,
                title=str(getattr(item, "text", "") or ""),
                parent_index=parent_index,
                docling_ref=ref,
                docling_parent_ref=parent_ref,
                docling_children_refs=children_refs,
                docling_depth=depth,
                heading_level=heading_level,
                content_layer=content_layer,
                hierarchy_status="corrected" if corrected else "original",
                hierarchy_source=("postprocess:docling-heading-level" if corrected else "docling"),
                provenance=provenance,
                content_data=_text_payload(item),
                metadata={"docling_section_groups": group_chain} if group_chain else {},
            )
            nodes.append(node)
            canonical_by_ref[ref] = len(nodes) - 1
            section_stack.append((heading_level, len(nodes) - 1))
            continue

        if ref in attached_to_float:
            owner_ref, _ = attached_to_float[ref]
            owner_index = canonical_by_ref.get(owner_ref)
            parent_index = nodes[owner_index].parent_index if owner_index is not None else None
            parent_index = parent_index if parent_index is not None else 0
        else:
            parent_index = nearest_mapped_parent(item, attach_to_active_section=True)

        if label in {"table", "document_index"}:
            node_type, title, text = "table", "", ""
            content_data = {"docling_item": _dump(item)}
        elif label in {"picture", "chart"}:
            node_type, title, text = "picture", "", ""
            content_data = {"docling_item": _dump(item)}
        elif hasattr(item, "name") and not hasattr(item, "text"):
            node_type = "group"
            title = str(getattr(item, "name", "") or label)
            text = ""
            content_data = {}
        else:
            node_type, title = "paragraph", ""
            text = str(getattr(item, "text", "") or "")
            content_data = _text_payload(item)

        raw_parent_index = nearest_mapped_parent(item, attach_to_active_section=False)
        corrected = ref in attached_to_float or raw_parent_index != parent_index
        nodes.append(
            ContentNodeDraft(
                node_type=node_type,
                original_type=label or "document_item",
                title=title,
                text=text,
                parent_index=parent_index,
                docling_ref=ref,
                docling_parent_ref=parent_ref,
                docling_children_refs=children_refs,
                docling_depth=depth,
                content_layer=content_layer,
                hierarchy_status="corrected" if corrected else "original",
                hierarchy_source=(
                    "postprocess:attachment"
                    if ref in attached_to_float
                    else "postprocess:active-section"
                    if corrected
                    else "docling"
                ),
                provenance=provenance,
                content_data=content_data,
            )
        )
        canonical_by_ref[ref] = len(nodes) - 1

    for owner_ref, subtree in suppressed_subtrees.items():
        owner_index = canonical_by_ref.get(owner_ref)
        if owner_index is not None:
            nodes[owner_index].content_data["docling_subtree"] = subtree

    _finalize_ordinals(nodes)
    relations: list[ContentRelationDraft] = []
    relation_keys: set[tuple[Any, ...]] = set()

    def add_relation(
        relation_type: str,
        source_index: int,
        target_index: int | None,
        *,
        status: str,
        origin: str,
        source_text: str = "",
        target_docling_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = (relation_type, source_index, target_index, source_text, target_docling_ref)
        if key in relation_keys:
            return
        relation_keys.add(key)
        relations.append(
            ContentRelationDraft(
                relation_type=relation_type,
                source_index=source_index,
                target_index=target_index,
                status=status,
                origin=origin,
                source_text=source_text,
                target_docling_ref=target_docling_ref,
                metadata=metadata or {},
            )
        )

    for related_ref, (owner_ref, relation_type) in attached_to_float.items():
        source_index = canonical_by_ref.get(related_ref)
        target_index = canonical_by_ref.get(owner_ref)
        if source_index is None:
            continue
        add_relation(
            relation_type,
            source_index,
            target_index,
            status="resolved" if target_index is not None else "unresolved",
            origin="docling",
            source_text=nodes[source_index].text,
            target_docling_ref=owner_ref,
        )

    _add_rule_relations(nodes, relations, relation_keys, add_relation)
    return SegmentedContent(
        nodes=nodes,
        relations=relations,
        metadata={
            **parsed.metadata,
            "normalizer": "segmentation:docling-canonical-tree",
            "canonical_leaf_types": sorted(LEAF_TYPES),
        },
    )


def _section_ancestor(nodes: list[ContentNodeDraft], index: int) -> int:
    current: int | None = index
    while current is not None:
        if nodes[current].node_type == "section":
            return current
        current = nodes[current].parent_index
    return 0


def _add_rule_relations(
    nodes: list[ContentNodeDraft],
    relations: list[ContentRelationDraft],
    relation_keys: set[tuple[Any, ...]],
    add_relation: Any,
) -> None:
    """Recover only explicit numbered references with explainable rules."""
    del relation_keys  # deduplication is owned by add_relation
    leaf_indexes = [index for index, node in enumerate(nodes) if node.node_type in LEAF_TYPES]

    def reference_text(index: int) -> str:
        node = nodes[index]
        if node.node_type == "paragraph":
            return node.text
        if node.node_type == "table":
            cells = node.content_data.get("docling_item", {}).get("data", {}).get("table_cells", [])
            return "\n".join(str(cell.get("text", "")) for cell in cells)
        return ""

    def nearby_object(source_index: int, wanted: str, radius: int = 4) -> int | None:
        source = nodes[source_index]
        section = _section_ancestor(nodes, source_index)
        candidates = [
            index
            for index in leaf_indexes
            if nodes[index].node_type == wanted
            and _section_ancestor(nodes, index) == section
            and abs(nodes[index].reading_order - source.reading_order) <= radius
        ]
        if not candidates:
            return None
        nearest_distance = min(
            abs(nodes[index].reading_order - source.reading_order) for index in candidates
        )
        nearest = [
            index
            for index in candidates
            if abs(nodes[index].reading_order - source.reading_order) == nearest_distance
        ]
        return nearest[0] if len(nearest) == 1 else None

    object_anchors: dict[tuple[str, str], int] = {}
    for index, node in enumerate(nodes):
        if node.node_type != "paragraph":
            continue
        match = _CAPTION_RE.match(node.text)
        if match is None:
            continue
        wanted = "picture" if match.group("kind") == "图" else "table"
        target = nearby_object(index, wanted, radius=3)
        if target is None:
            continue
        number = match.group("number").lower()
        object_anchors[(wanted, number)] = target
        add_relation(
            "caption_of",
            index,
            target,
            status="resolved",
            origin="rule:numbered-caption-adjacency",
            source_text=match.group(0),
            target_docling_ref=nodes[target].docling_ref,
            metadata={"rule": "numbered caption within three reading-order nodes"},
        )

    # Docling-provided captions are authoritative anchors even when their
    # original label is not CAPTION.
    for relation in relations:
        if relation.relation_type != "caption_of" or relation.target_index is None:
            continue
        match = _CAPTION_RE.match(nodes[relation.source_index].text)
        if match:
            object_anchors[
                (nodes[relation.target_index].node_type, match.group("number").lower())
            ] = relation.target_index

    section_anchors: dict[str, int] = {}
    for index, node in enumerate(nodes):
        if node.node_type != "section":
            continue
        match = _SECTION_NUMBER_RE.match(node.title)
        if match:
            section_anchors.setdefault(match.group("number"), index)

    float_matches: dict[int, list[re.Match[str]]] = {
        index: list(_FLOAT_REF_RE.finditer(reference_text(index)))
        for index, node in enumerate(nodes)
        if node.node_type in {"paragraph", "table"} and reference_text(index)
    }
    # A paragraph that names exactly one figure/table and says it is shown here
    # can deterministically anchor that number to the sole nearby object.  Build
    # all such anchors before resolving multi-reference paragraphs.
    for index, matches in float_matches.items():
        text = reference_text(index)
        if (
            nodes[index].node_type != "paragraph"
            or len(matches) != 1
            or ("所示" not in text and "如下" not in text)
        ):
            continue
        match = matches[0]
        wanted = "picture" if match.group("kind") == "图" else "table"
        target = nearby_object(index, wanted)
        if target is not None:
            object_anchors.setdefault((wanted, match.group("number").lower()), target)

    for index, node in enumerate(nodes):
        text = reference_text(index)
        if node.node_type not in {"paragraph", "table"} or not text:
            continue
        for match in _SECTION_REF_RE.finditer(text):
            number = match.group("number")
            target = section_anchors.get(number)
            add_relation(
                "explicit_reference",
                index,
                target,
                status="resolved" if target is not None else "unresolved",
                origin="rule:explicit-section-number",
                source_text=match.group(0),
                target_docling_ref=nodes[target].docling_ref if target is not None else None,
                metadata={"reference_kind": "section", "number": number},
            )
        matches = float_matches.get(index, [])
        for match in matches:
            wanted = "picture" if match.group("kind") == "图" else "table"
            number = match.group("number").lower()
            target = object_anchors.get((wanted, number))
            origin = "rule:explicit-numbered-object"
            if (
                target is None
                and node.node_type == "paragraph"
                and len(matches) == 1
                and ("所示" in text or "如下" in text or "见" in match.group(0))
            ):
                target = nearby_object(index, wanted)
                if target is not None:
                    origin = "rule:nearby-explicit-reference"
            add_relation(
                "explicit_reference",
                index,
                target,
                status="resolved" if target is not None else "unresolved",
                origin=origin,
                source_text=match.group(0),
                target_docling_ref=nodes[target].docling_ref if target is not None else None,
                metadata={"reference_kind": wanted, "number": number},
            )


def segment(parsed: ParsedContent) -> SegmentedContent:
    """Produce the canonical tree; no chunk-size argument exists by design."""
    if parsed.docling_document is not None:
        return _normalize_docling(parsed)
    nodes = list(parsed.nodes)
    _finalize_ordinals(nodes)
    return SegmentedContent(
        nodes=nodes,
        metadata={**parsed.metadata, "normalizer": "segmentation:canonical-tree"},
    )
