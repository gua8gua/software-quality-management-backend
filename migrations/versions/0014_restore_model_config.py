"""Restore global model configuration, independently of the project content schema."""
from alembic import op

from app.modules.model_config.models import MODEL_CONFIG_TABLES

revision = "0014_restore_model_config"
down_revision = "0013_original_file_types"
branch_labels = None
depends_on = None


def upgrade():
    for table in MODEL_CONFIG_TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for table in reversed(MODEL_CONFIG_TABLES):
        table.drop(op.get_bind())
