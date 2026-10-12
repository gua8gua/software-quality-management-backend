from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.ollama import OllamaEmbeddingProvider
from app.providers.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.embeddings.sentence_transformers import SentenceTransformerEmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider

SessionDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_llm_provider(settings: SettingsDep) -> LLMProvider | None:
    if not settings.llm_enabled:
        return None
    if settings.llm_provider != "openai_compatible":
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
    return OpenAICompatibleLLMProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        model_version=settings.llm_model_version,
        timeout_seconds=settings.llm_timeout_seconds,
        native_json_schema=settings.llm_native_json_schema,
        chat_template_enable_thinking=settings.llm_chat_template_enable_thinking,
        tokenize_url=settings.llm_tokenize_url,
    )


def get_embedding_provider(settings: SettingsDep) -> EmbeddingProvider:
    if settings.embedding_provider == "ollama":
        if not settings.embedding_base_url:
            raise ValueError("EMBEDDING_BASE_URL is required for Ollama embeddings")
        return OllamaEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            model_version=settings.embedding_revision,
            batch_size=settings.embedding_batch_size,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    if settings.embedding_provider == "openai_compatible":
        if not settings.embedding_base_url:
            raise ValueError("EMBEDDING_BASE_URL is required for openai_compatible embeddings")
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            model_version=settings.embedding_revision,
            batch_size=settings.embedding_batch_size,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    if settings.embedding_provider != "sentence_transformers":
        raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {settings.embedding_provider}")
    return SentenceTransformerEmbeddingProvider(
        settings.embedding_model,
        revision=settings.embedding_revision,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
    )
