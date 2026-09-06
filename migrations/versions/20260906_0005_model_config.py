"""Add encrypted model connections and per-task bindings."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_0005"
down_revision = "20260906_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("is_local", sa.Boolean(), nullable=False),
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    op.create_table(
        "model_task_bindings",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("task", sa.String(64), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("model_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=True),
        sa.Column("test_status", sa.String(24), nullable=False),
        sa.Column("test_message", sa.Text(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("model_task_bindings")
    op.drop_table("model_connections")
