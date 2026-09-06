"""Add project catalog and original uploaded files without changing prior TLR snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "20260905_0003"
down_revision = "20260905_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tlr_projects",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        "INSERT INTO tlr_projects (tenant_id, id, name, description, created_at) "
        "SELECT tenant_id, project_id, project_id, '', MIN(created_at) "
        "FROM tlr_datasets GROUP BY tenant_id, project_id"
    )
    op.create_table(
        "tlr_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
    )
    with op.batch_alter_table("tlr_artifacts") as batch:
        batch.add_column(sa.Column("original_file_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_tlr_artifacts_original_file", "tlr_files", ["original_file_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("tlr_artifacts") as batch:
        batch.drop_constraint("fk_tlr_artifacts_original_file", type_="foreignkey")
        batch.drop_column("original_file_id")
    op.drop_table("tlr_files")
    op.drop_table("tlr_projects")
