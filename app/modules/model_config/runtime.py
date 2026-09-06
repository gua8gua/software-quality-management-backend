# ruff: noqa: E501
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.model_config.models import ModelConnection, ModelTaskBinding
from app.modules.model_config.vault import CredentialVault
from app.providers.embedding.base import EmbeddingProvider
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


async def resolve_tlr_models(
    session: AsyncSession,
    settings: Settings,
    tenant_id: str,
    fallback_embedding: EmbeddingProvider,
    fallback_llm: LLMProvider,
) -> tuple[EmbeddingProvider, LLMProvider, LLMProvider, dict]:
    rows = list((await session.scalars(select(ModelTaskBinding).where(ModelTaskBinding.tenant_id == tenant_id))).all())
    bindings = {row.task: row for row in rows}
    connections = {}
    for row in rows:
        connection = await session.get(ModelConnection, row.connection_id)
        if connection is not None and connection.tenant_id == tenant_id:
            connections[row.connection_id] = connection
    vault = CredentialVault(settings)

    def credentials(task: str):
        binding = bindings.get(task)
        connection = connections.get(binding.connection_id) if binding else None
        if not binding or not connection:
            return None
        key = vault.decrypt(connection.api_key_ciphertext) or ("local" if connection.is_local else "")
        snapshot = {
            "source": "database",
            "connection_id": connection.id,
            "connection_name": connection.name,
            "base_url": connection.base_url,
            "is_local": connection.is_local,
            "model_id": binding.model_id,
        }
        return binding, connection, key, snapshot

    selected = credentials("tlr_embedding")
    if selected:
        binding, connection, key, embedding_snapshot = selected
        embedding = OpenAICompatibleEmbeddingProvider(base_url=connection.base_url, api_key=key, model=binding.model_id, expected_dimension=binding.dimension or settings.embedding_dimension, timeout_seconds=settings.model_timeout_seconds, trust_env=not connection.is_local)
    else:
        embedding, embedding_snapshot = fallback_embedding, {"source": ".env", "base_url": settings.model_base_url, "model_id": settings.embedding_model, "is_local": False}

    def llm_for(task: str, fallback: LLMProvider):
        selected_llm = credentials(task)
        if not selected_llm:
            return fallback, {"source": ".env", "base_url": settings.llm_base_url, "model_id": settings.llm_model, "is_local": False}
        binding, connection, key, snapshot = selected_llm
        return OpenAICompatibleLLMProvider(base_url=connection.base_url, api_key=key, model=binding.model_id, timeout_seconds=settings.llm_timeout_seconds, trust_env=not connection.is_local), snapshot

    classifier, classifier_snapshot = llm_for("tlr_classification", fallback_llm)
    if bindings.get("architecture_extraction"):
        architecture, architecture_snapshot = llm_for("architecture_extraction", classifier)
    else:
        architecture = classifier
        architecture_snapshot = {
            **classifier_snapshot,
            "source": "tlr_classification fallback",
        }
    return embedding, classifier, architecture, {
        "tlr_embedding": embedding_snapshot,
        "tlr_classification": classifier_snapshot,
        "architecture_extraction": architecture_snapshot,
    }
