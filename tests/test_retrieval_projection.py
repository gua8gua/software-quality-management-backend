from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    RichTableCell,
    TableCell,
    TableData,
)

from app.modules.artifacts.models import Artifact, ContentNode, ContentRelation
from app.modules.traceability.projections import raw_projection


def test_table_projection_uses_docling_serializer_and_derived_heading_path():
    doc = DoclingDocument(name="projection")
    item = doc.add_table(
        data=TableData(
            num_rows=2,
            num_cols=1,
            table_cells=[
                TableCell(
                    text="Header value",
                    row_span=2,
                    col_span=1,
                    start_row_offset_idx=0,
                    end_row_offset_idx=2,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    column_header=True,
                )
            ],
        )
    )
    root = ContentNode(
        id="root",
        artifact_id="artifact",
        parent_id=None,
        node_type="document",
        original_type="document",
        title="Design document",
        reading_order=0,
    )
    section = ContentNode(
        id="section",
        artifact_id="artifact",
        parent_id="root",
        node_type="section",
        original_type="section_header",
        title="4 Database design",
        reading_order=1,
    )
    subsection = ContentNode(
        id="subsection",
        artifact_id="artifact",
        parent_id="section",
        node_type="section",
        original_type="section_header",
        title="4.1 Tables",
        reading_order=2,
    )
    caption = ContentNode(
        id="caption",
        artifact_id="artifact",
        parent_id="subsection",
        node_type="paragraph",
        original_type="caption",
        text="Table 4-1 Storage fields",
        reading_order=3,
    )
    table = ContentNode(
        id="table",
        artifact_id="artifact",
        parent_id="subsection",
        node_type="table",
        original_type="table",
        reading_order=4,
        content_json={"docling_item": item.model_dump(mode="json")},
    )
    relation = ContentRelation(
        id="relation",
        artifact_id="artifact",
        source_node_id="caption",
        target_node_id="table",
        relation_type="caption_of",
        origin="docling",
    )
    artifact = Artifact(
        id="artifact",
        project_id="project",
        external_id="sds",
        engineering_role="design",
    )
    path, text = raw_projection(
        artifact,
        table,
        {item.id: item for item in (root, section, subsection, caption, table)},
        [relation],
    )
    assert path == ["Design document", "4 Database design", "4.1 Tables"]
    assert "Table 4-1 Storage fields" in text
    assert "<table" in text
    assert "Header value" in text
    assert 'rowspan="2"' in text
    assert "reading_order" not in text
    assert "node_id" not in text


def test_table_projection_rebuilds_non_contiguous_rich_cell_refs():
    doc = DoclingDocument(name="rich-table")
    doc.add_group(parent=doc.body)
    doc.add_text(label=DocItemLabel.TEXT, text="ignored zero", parent=doc.body)
    doc.add_text(label=DocItemLabel.TEXT, text="ignored one", parent=doc.body)
    cell_group = doc.add_group(parent=doc.body)
    cell_text = doc.add_text(label=DocItemLabel.TEXT, text="Rich cell value", parent=cell_group)
    item = doc.add_table(
        data=TableData(
            num_rows=1,
            num_cols=1,
            table_cells=[
                RichTableCell(
                    text="Rich cell value",
                    ref=cell_group.get_ref(),
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                )
            ],
        )
    )
    node = ContentNode(
        id="table",
        artifact_id="artifact",
        parent_id=None,
        node_type="table",
        original_type="table",
        reading_order=0,
        content_json={
            "docling_item": item.model_dump(mode="json"),
            "docling_subtree": [
                cell_group.model_dump(mode="json"),
                cell_text.model_dump(mode="json"),
            ],
        },
    )
    artifact = Artifact(
        id="artifact",
        project_id="project",
        external_id="sds",
        engineering_role="design",
    )

    _, text = raw_projection(artifact, node, {node.id: node}, [])

    assert "Rich cell value" in text
    assert "<table" in text
