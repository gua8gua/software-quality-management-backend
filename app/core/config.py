from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    tlr_no_progress_seconds: float = Field(default=900, gt=0)
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Software Quality Management Backend"
    app_env: str = "local"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/software_quality_management"
    )
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_to_file: bool = True
    log_rotation: str = "size"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_backup_count: int = Field(default=10, ge=0)
    log_use_color: bool | None = None
    llm_enabled: bool = False
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "http://127.0.0.1:18000/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen35-9b"
    llm_model_version: str = ""
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    review_model_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_native_json_schema: bool = False
    llm_chat_template_enable_thinking: bool | None = None
    llm_context_window: int = Field(default=16_384, ge=1_024)
    llm_tokenize_url: str = ""
    llm_context_safety_tokens: int = Field(default=512, ge=0)
    trace_fine_max_tokens: int = Field(default=768, ge=64)
    embedding_provider: str = "sentence_transformers"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = Field(default=60.0, gt=0)
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_revision: str = "main"
    embedding_device: str | None = None
    embedding_batch_size: int = Field(default=8, ge=1, le=256)
    model_timeout_seconds: float = Field(default=60.0, gt=0)
    review_worker_lease_seconds: int = Field(default=300, ge=30)
    model_config_encryption_key: str = ""
    model_config_key_file: str = "data/model-config.key"


@lru_cache
def get_settings() -> Settings:
    return Settings()
