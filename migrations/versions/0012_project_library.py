"""Destructive library simplification: no legacy type conversion or dual model.

Projects containing unsupported artifacts are deleted with their dependents.
Valid requirement/design documents and already-conforming code remain unchanged.
"""

import sqlalchemy as sa
from alembic import op

from app.modules.artifacts.cleanup import project_deletions

revision = "0012_project_library"
down_revision = "0011_passage_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    old = sa.MetaData()
    old.reflect(bind=connection)
    artifacts = old.tables["artifacts"]
    valid_type = (
        "(modality = 'text' AND engineering_role IS NOT NULL AND engineering_role IN ('requirement', 'design')) "
        "OR (modality = 'code' AND engineering_role IS NULL)"
    )
    invalid_projects = list(connection.scalars(sa.select(artifacts.c.project_id).where(
        sa.text(f"NOT ({valid_type})")
    ).distinct()))
    for project_id in invalid_projects:
        for statement in project_deletions(old, project_id):
            connection.execute(statement)
    if invalid_projects:
        print(f"Deleted incompatible projects and dependent data: {invalid_projects}")

    op.create_table(
        "folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(128), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.UniqueConstraint("project_id", "id"),
        sa.ForeignKeyConstraint(["project_id", "parent_id"], ["folders.project_id", "folders.id"]),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_folder_not_self"),
    )
    op.create_index("ix_folders_project_id", "folders", ["project_id"])
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])
    op.create_index("uq_folder_sibling_name", "folders", ["project_id", sa.text("coalesce(parent_id, '')"), "name"], unique=True)
    with op.batch_alter_table("artifacts") as batch:
        for name in ("participation", "locator", "tags", "metadata_json", "inline_content"):
            batch.drop_column(name)
        batch.alter_column("engineering_role", existing_type=sa.String(32), nullable=True, server_default=None)
        batch.add_column(sa.Column("folder_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_artifact_folder", "folders", ["project_id", "folder_id"], ["project_id", "id"])
        batch.create_index("ix_artifacts_folder_id", ["folder_id"])
        batch.create_check_constraint("ck_artifact_type", valid_type)


def downgrade() -> None:
    raise RuntimeError("Destructive library cleanup is not reversible; restore a database backup instead")
