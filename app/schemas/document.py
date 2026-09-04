from pydantic import BaseModel, ConfigDict, Field


class DocumentUpload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    kb_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2_000_000)


class DocumentUploaded(BaseModel):
    document_id: str
    kb_id: str
    status: int
    chunk_count: int

