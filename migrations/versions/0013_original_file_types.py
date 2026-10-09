"""Backfill MIME from stored bytes. Canonical/analysis data is not preview data."""

import sqlalchemy as sa
from alembic import op

from app.modules.artifacts.file_types import detect_file_type

revision = "0013_original_file_types"
down_revision = "0012_project_library"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("files", sa.Column("file_format", sa.String(32), nullable=False, server_default=""))
    connection = op.get_bind()
    ids = list(connection.scalars(sa.text("SELECT id FROM files")))
    for file_id in ids:
        row = connection.execute(sa.text("SELECT filename, payload FROM files WHERE id=:id"), {"id": file_id}).one()
        extension, mime = detect_file_type(row.filename, row.payload)
        connection.execute(sa.text("UPDATE files SET file_format=:format, media_type=:mime WHERE id=:id"),
                           {"id": file_id, "format": extension, "mime": mime})


def downgrade():
    with op.batch_alter_table("files") as batch:
        batch.drop_column("file_format")
