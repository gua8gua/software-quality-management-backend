from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Software Quality Management Backend"
    app_env: str = "local"

    # 日志配置
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_to_file: bool = True
    log_rotation: str = "size"  # "size" | "time"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_backup_count: int = Field(default=10, ge=0)
    log_use_color: bool | None = None  # None=自动（TTY 时启用彩色）

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/software_quality_management"

    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=1536, gt=0)
    model_timeout_seconds: float = Field(default=30.0, gt=0)

    vector_store: str = "pgvector"
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=5, gt=0, le=100)

    # 通用大模型（OpenAI 兼容接口）—— 重排、改写、意图识别等能力统一走这里
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)

    # 重排（rerank）
    rerank_enabled: bool = False
    rerank_provider: str = "llm"  # "llm" | "noop"
    rerank_top_n: int = Field(default=5, gt=0, le=100)
    rerank_max_candidates: int = Field(default=20, gt=0)
    rerank_chunk_max_chars: int = Field(default=1000, gt=0)
    rerank_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    # TLR module limits; Java dependency is optional and explicitly selected per run.
    tlr_run_timeout_seconds: float = Field(default=1800, gt=0)
    tlr_max_elements: int = Field(default=5000, gt=0)
    tlr_max_candidates: int = Field(default=20_000, gt=0)
    tlr_max_comparisons: int = Field(default=2_000_000, gt=0)
    tlr_retrieval_timeout_seconds: float = Field(default=120, gt=0)
    tlr_lissa_jar: str = (
        "vendor/lissa/LiSSA-RATLR-V2/lissa/target/"
        "ratlr-0.2.0-SNAPSHOT-jar-with-dependencies.jar"
    )
    tlr_lissa_bridge_dir: str = "artifacts/lissa/bridge"
    tlr_java_command: str = "java"

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

