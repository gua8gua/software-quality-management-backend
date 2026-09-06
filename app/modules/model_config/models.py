# ruff: noqa: E501, I001
from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModelConnection(Base):
    __tablename__ = "model_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(Text)
    is_local: Mapped[bool] = mapped_column(Boolean)
    api_key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    models: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="unchecked")
    status_message: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ModelTaskBinding(Base):
    __tablename__ = "model_task_bindings"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("model_connections.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[str] = mapped_column(String(255))
    dimension: Mapped[int | None] = mapped_column(Integer)
    test_status: Mapped[str] = mapped_column(String(24), default="untested")
    test_message: Mapped[str] = mapped_column(Text, default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


MODEL_CONFIG_TABLES = [ModelConnection.__table__, ModelTaskBinding.__table__]
