from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContentNodeDraft:
    """One node in the canonical content tree.

    ``text`` is source text only. Structural nodes do not aggregate descendant
    text, and tables/pictures keep their native payload in ``content_data``.
    """

    node_type: str
    original_type: str
    title: str = ""
    text: str = ""
    parent_index: int | None = None
    sibling_ordinal: int = 0
    reading_order: int = 0
    docling_ref: str | None = None
    docling_parent_ref: str | None = None
    docling_children_refs: list[str] = field(default_factory=list)
    docling_depth: int | None = None
    heading_level: int | None = None
    content_layer: str = "body"
    hierarchy_status: str = "original"
    hierarchy_source: str = "parser"
    provenance: list[dict[str, Any]] = field(default_factory=list)
    content_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentRelationDraft:
    relation_type: str
    source_index: int
    target_index: int | None
    status: str
    origin: str
    source_text: str = ""
    target_docling_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedContent:
    modality: str
    file_format: str
    language: str
    nodes: list[ContentNodeDraft] = field(default_factory=list)
    docling_document: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SegmentedContent:
    nodes: list[ContentNodeDraft]
    relations: list[ContentRelationDraft] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
