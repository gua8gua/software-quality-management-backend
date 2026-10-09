"""Resolve global model choices against the current providers, not legacy TLR."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.model_config.console_router import model_transport
from app.modules.model_config.models import ModelConnection, ModelTaskBinding
from app.modules.model_config.vault import CredentialVault
from app.providers.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


async def select_tlr_models(session: AsyncSession, settings: Settings, tenant: str, llm, embedding):
    if await session.get(ModelTaskBinding, (tenant, "tlr_llm")):
        base, key, model, local = await model_transport(session, settings, tenant, "tlr_llm")
        llm = OpenAICompatibleLLMProvider(base_url=base, api_key=key, model=model, timeout_seconds=settings.llm_timeout_seconds, native_json_schema=settings.llm_native_json_schema, chat_template_enable_thinking=settings.llm_chat_template_enable_thinking, trust_env=not local)
    binding = await session.get(ModelTaskBinding, (tenant, "tlr_embedding"))
    if binding:
        connection = await session.get(ModelConnection, binding.connection_id)
        if connection is None or connection.tenant_id != tenant:
            raise ValueError("Embedding model connection scope is invalid")
        embedding = OpenAICompatibleEmbeddingProvider(base_url=connection.base_url, api_key=CredentialVault(settings).decrypt(connection.api_key_ciphertext) or "local", model=binding.model_id, timeout_seconds=settings.embedding_timeout_seconds, trust_env=not connection.is_local)
    return llm, embedding
