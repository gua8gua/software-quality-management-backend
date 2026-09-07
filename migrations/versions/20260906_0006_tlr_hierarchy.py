"""Persist dataset-owned Artifact hierarchy nodes.

Revision ID: 20260906_0006
Revises: 20260906_0005
"""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20260906_0006"
down_revision = "20260906_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tlr_hierarchy_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("node_key", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["tlr_artifacts.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["tlr_datasets.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["tlr_hierarchy_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "node_key"),
    )
    op.create_index(
        op.f("ix_tlr_hierarchy_nodes_dataset_id"),
        "tlr_hierarchy_nodes",
        ["dataset_id"],
        unique=False,
    )
    bind = op.get_bind()
    artifacts = list(
        bind.execute(
            sa.text(
                "SELECT id, dataset_id, external_id, kind, structure FROM tlr_artifacts"
            )
        ).mappings()
    )
    node_ids = {
        (row["dataset_id"], row["external_id"]): str(uuid4()) for row in artifacts
    }
    hierarchy = sa.table(
        "tlr_hierarchy_nodes",
        sa.column("id", sa.String),
        sa.column("dataset_id", sa.String),
        sa.column("parent_id", sa.String),
        sa.column("artifact_id", sa.String),
        sa.column("node_key", sa.String),
        sa.column("title", sa.String),
        sa.column("node_type", sa.String),
        sa.column("ordinal", sa.Integer),
        sa.column("metadata_json", sa.JSON),
    )
    pending_parents = []
    values = []
    for ordinal, row in enumerate(artifacts):
        structure = row["structure"] or {}
        if isinstance(structure, str):
            structure = json.loads(structure)
        parents = [
            value
            for value in structure.get("parent_ids", [])
            if (row["dataset_id"], value) in node_ids
        ]
        pure = row["kind"] == "package" or structure.get("content_status") == "reference_only"
        identifier = node_ids[(row["dataset_id"], row["external_id"])]
        values.append(
            {
                "id": identifier,
                "dataset_id": row["dataset_id"],
                "parent_id": None,
                "artifact_id": None if pure else row["id"],
                "node_key": f"artifact:{row['external_id']}",
                "title": structure.get("title")
                or structure.get("original_id")
                or row["external_id"],
                "node_type": structure.get("original_type")
                or ("group" if pure else "artifact"),
                "ordinal": ordinal,
                "metadata_json": {
                    "source": "artifact.structure",
                    "legacy_external_id": row["external_id"],
                    "all_parent_ids": parents,
                    "content_status": structure.get("content_status"),
                },
            }
        )
        if parents:
            pending_parents.append(
                (identifier, node_ids[(row["dataset_id"], parents[0])])
            )
    if values:
        op.bulk_insert(hierarchy, values)
    for identifier, parent_id in pending_parents:
        bind.execute(
            hierarchy.update().where(hierarchy.c.id == identifier).values(parent_id=parent_id)
        )
    op.create_index(
        op.f("ix_tlr_hierarchy_nodes_parent_id"),
        "tlr_hierarchy_nodes",
        ["parent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tlr_hierarchy_nodes_artifact_id"),
        "tlr_hierarchy_nodes",
        ["artifact_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_tlr_hierarchy_nodes_artifact_id"), table_name="tlr_hierarchy_nodes")
    op.drop_index(op.f("ix_tlr_hierarchy_nodes_parent_id"), table_name="tlr_hierarchy_nodes")
    op.drop_index(op.f("ix_tlr_hierarchy_nodes_dataset_id"), table_name="tlr_hierarchy_nodes")
    op.drop_table("tlr_hierarchy_nodes")
