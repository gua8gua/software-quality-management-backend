# ruff: noqa: I001
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


TaskId = Literal["tlr_embedding", "tlr_classification", "architecture_extraction"]


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: SecretStr | None = None


class ModelItem(BaseModel):
    id: str
    owned_by: str = "unknown"


class ConnectionView(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    is_local: bool
    api_key_configured: bool
    models: list[ModelItem]
    status: str
    status_message: str
    last_checked_at: datetime | None


class BindingInput(BaseModel):
    connection_id: str
    model_id: str = Field(min_length=1, max_length=255)
    dimension: int | None = Field(default=None, gt=0, le=100_000)


class BindingView(BaseModel):
    task: TaskId
    connection_id: str | None = None
    model_id: str | None = None
    dimension: int | None = None
    test_status: str = "unconfigured"
    test_message: str = ""
    last_tested_at: datetime | None = None
    fallback: dict | None = None


class TaskInfo(BaseModel):
    id: TaskId
    label: str
    description: str
    capability: Literal["embedding", "chat"]


class ConfigView(BaseModel):
    connections: list[ConnectionView]
    tasks: list[TaskInfo]
    bindings: list[BindingView]
