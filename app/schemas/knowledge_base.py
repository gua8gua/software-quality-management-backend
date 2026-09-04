from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    tenant_id: str = Field(min_length=1, max_length=128)


class KnowledgeBaseCreated(BaseModel):
    kb_id: str
    name: str
    tenant_id: str
    created_at: datetime

