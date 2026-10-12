from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    GroupLabel,
    ProvenanceItem,
    TableCell,
    TableData,
)

from app.modules.parsing.models import ParsedContent
from app.modules.segmentation.service import segment


def test_docling_normalization_preserves_native_content_and_relations():
    doc = DoclingDocument(name="sample")
    section = doc.add_heading("1 Overview", level=1)
    group = doc.add_group(label=GroupLabel.LIST, name="list", parent=section)
    doc.add_text(label=DocItemLabel.LIST_ITEM, text="Original paragraph", parent=group)
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="图1 Architecture", parent=section)
    picture = doc.add_picture(caption=caption, parent=section)
    doc.add_text(
        label=DocItemLabel.TEXT,
        text="见第2.1节，如图1所示。",
        parent=section,
        prov=ProvenanceItem(
            page_no=1,
            bbox=BoundingBox(l=1, t=10, r=20, b=1),
            charspan=(0, 13),
        ),
    )
    doc.add_text(label=DocItemLabel.TEXT, text="另见表99。", parent=section)
    subsection = doc.add_heading("2.1 Details", level=2, parent=section)
    table = doc.add_table(
        data=TableData(
            num_rows=1,
            num_cols=1,
            table_cells=[
                TableCell(
                    text="value",
                    row_span=1,
                    col_span=1,
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    column_header=True,
                )
            ],
        ),
        parent=subsection,
    )

    result = segment(
        ParsedContent(
            modality="text",
            file_format="pdf",
            language="",
            docling_document=doc,
            metadata={"parser": "docling"},
        )
    )

    types = [node.node_type for node in result.nodes]
    assert set(types) == {"document", "section", "group", "paragraph", "table", "picture"}
    assert "semantic_unit" not in types
    assert all(node.parent_index is not None for node in result.nodes[1:])
    assert all(node.parent_index < index for index, node in enumerate(result.nodes) if index)

    table_node = next(node for node in result.nodes if node.docling_ref == table.self_ref)
    assert table_node.content_data["docling_item"]["data"]["table_cells"][0]["text"] == "value"
    picture_node = next(node for node in result.nodes if node.docling_ref == picture.self_ref)
    assert picture_node.text == ""
    section_node = next(node for node in result.nodes if node.title == "1 Overview")
    assert section_node.text == ""
    paragraph = next(node for node in result.nodes if node.text.startswith("见第2.1节"))
    assert paragraph.provenance[0]["page_no"] == 1

    relation_types = {(row.relation_type, row.status) for row in result.relations}
    assert ("caption_of", "resolved") in relation_types
    assert ("explicit_reference", "resolved") in relation_types
    assert ("explicit_reference", "unresolved") in relation_types


def test_body_level_docling_blocks_attach_to_active_multilevel_sections():
    doc = DoclingDocument(name="body-parented")
    top = doc.add_heading("3 Requirements", level=1, parent=doc.body)
    first = doc.add_text(
        label=DocItemLabel.TEXT, text="top section paragraph", parent=doc.body
    )
    nested = doc.add_heading("3.1 Functional", level=2, parent=doc.body)
    second = doc.add_text(
        label=DocItemLabel.TEXT, text="nested section paragraph", parent=doc.body
    )

    result = segment(
        ParsedContent(
            modality="text",
            file_format="docx",
            language="",
            docling_document=doc,
            metadata={"parser": "docling"},
        )
    )
    by_ref = {node.docling_ref: (index, node) for index, node in enumerate(result.nodes)}
    top_index, top_node = by_ref[top.self_ref]
    nested_index, nested_node = by_ref[nested.self_ref]
    _, first_node = by_ref[first.self_ref]
    _, second_node = by_ref[second.self_ref]

    assert first_node.parent_index == top_index
    assert nested_node.parent_index == top_index
    assert second_node.parent_index == nested_index
    assert first_node.docling_parent_ref == "#/body"
    assert second_node.docling_parent_ref == "#/body"
    assert first_node.hierarchy_source == "postprocess:active-section"
    assert top_node.node_type == "section"
