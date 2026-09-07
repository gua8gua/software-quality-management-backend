# ruff: noqa: I001
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


TaskId = Literal["tlr_embedding", "tlr_classification", "architecture_extraction"]
ModelCapability = Literal[
    "chat", "embedding", "rerank", "vision", "image_generation",
    "speech", "transcription", "unknown",
]


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: Literal["deepseek", "local_openai", "custom"] = "custom"
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    api_key: SecretStr | None = None


class ProviderOption(BaseModel):
    id: Literal["deepseek", "local_openai", "custom"]
    label: str
    description: str
    default_base_url: str | None = None
    base_url_editable: bool
    api_key_required: bool
    is_local: bool
    capabilities: list[Literal["embedding", "chat"]]


class ModelItem(BaseModel):
    id: str
    owned_by: str = "unknown"
    capabilities: list[ModelCapability] = Field(default_factory=lambda: ["unknown"])
    capability_source: Literal["provider", "ollama", "probe", "manual", "unknown"] = "unknown"
    format: str | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    embedding_dimension: int | None = None
    verification: dict[str, str] = Field(default_factory=dict)


class ModelMetadataInput(BaseModel):
    capabilities: list[ModelCapability] = Field(min_length=1)


class ConnectionView(BaseModel):
    id: str
    name: str
    provider: str
    provider_label: str
    capabilities: list[Literal["embedding", "chat"]]
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
    providers: list[ProviderOption]
    connections: list[ConnectionView]
    tasks: list[TaskInfo]
    bindings: list[BindingView]
