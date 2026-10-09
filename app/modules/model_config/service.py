# ruff: noqa: E501, E701, E702, I001
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.model_config.models import ModelConnection, ModelTaskBinding
from app.modules.model_config.schemas import BindingInput, BindingView, ConfigView, ConnectionInput, ConnectionView, ModelItem, ModelMetadataInput, ProviderOption, TaskInfo
from app.modules.model_config.vault import CredentialVault
from app.providers.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


TASKS = [
    TaskInfo(id="review_analysis", label="一致性分析与修改建议", description="已有 TLR 后的 Agents SDK 评审、证据调查、总体评价和建议验证；不重新生成追溯或向量。", capability="chat", group="analysis", group_label="一致性分析"),
    TaskInfo(id="tlr_embedding", label="TLR 向量编码", description="规范内容树的检索投影向量编码，不自动重建已有向量。", capability="embedding", group="tlr", group_label="TLR"),
    TaskInfo(id="tlr_llm", label="TLR 语义标注与 Fine Trace", description="当前正式 TLR 的标注及候选判断模型。", capability="chat", group="tlr", group_label="TLR"),
    TaskInfo(id="agent_chat", label="项目对话", description="通过信息工具查询当前项目的真实数据。", capability="chat", group="agent", group_label="项目对话"),
    TaskInfo(id="peer_llm", label="原有需求与报告工具", description="原有需求拆分、需求代码映射与报告功能，保持原实现。", capability="chat", group="peer", group_label="原有工具"),
]
TASK_MAP = {task.id: task for task in TASKS}
PROVIDERS = [
    ProviderOption(id="deepseek", label="DeepSeek 官方 API", description="只需填写 DeepSeek API 密钥，地址由后端维护；当前用于对话任务。", default_base_url="https://api.deepseek.com", base_url_editable=False, api_key_required=True, is_local=False, capabilities=["chat"]),
    ProviderOption(id="local_openai", label="本地 OpenAI 兼容服务", description="适用于 Ollama、vLLM、LM Studio 等本地推理服务，实际能力以任务实测为准。", default_base_url="http://127.0.0.1:11434/v1", base_url_editable=True, api_key_required=False, is_local=True, capabilities=["embedding", "chat"]),
    ProviderOption(id="custom", label="其他 OpenAI 兼容 API", description="填写自建或其他云端兼容接口地址，实际能力以任务实测为准。", base_url_editable=True, api_key_required=True, is_local=False, capabilities=["embedding", "chat"]),
]
PROVIDER_MAP = {provider.id: provider for provider in PROVIDERS}


def normalize_url(raw: str) -> tuple[str, bool]:
    parsed = urlsplit(raw.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppError(ErrorCode.DATA_INVALID, "模型地址必须是无凭据、查询参数和片段的 HTTP(S) 地址")
    host = parsed.hostname.lower()
    local = host in {"localhost", "host.docker.internal"} or host.endswith(".local")
    try:
        local = local or ip_address(host).is_private or ip_address(host).is_loopback or ip_address(host).is_link_local
    except ValueError:
        pass
    if not local and parsed.scheme != "https":
        raise AppError(ErrorCode.DATA_INVALID, "远程模型地址必须使用 HTTPS")
    netloc = f"[{host}]" if ":" in host else host
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")), local



def ollama_native_root(base_url: str) -> str:
    """Return the native Ollama root for an OpenAI-compatible /v1 URL."""
    parsed = urlsplit(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def ollama_model_metadata(payload: dict) -> dict:
    native = payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else []
    mapping = {"completion": "chat", "embedding": "embedding", "vision": "vision"}
    capabilities = sorted({mapping[item] for item in native if item in mapping}) or ["unknown"]
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    model_info = payload.get("model_info") if isinstance(payload.get("model_info"), dict) else {}
    dimensions = [value for key, value in model_info.items() if key.endswith(".embedding_length") and isinstance(value, int)]
    return {
        "capabilities": capabilities,
        "capability_source": "ollama",
        "format": details.get("format"),
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "embedding_dimension": dimensions[0] if "embedding" in capabilities and dimensions else None,
        "verification": {},
    }


class ModelConfigService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session, self.settings, self.vault = session, settings, CredentialVault(settings)

    async def view(self, tenant: str) -> ConfigView:
        connections = list((await self.session.scalars(select(ModelConnection).where(ModelConnection.tenant_id == tenant).order_by(ModelConnection.name))).all())
        bindings = {row.task: row for row in (await self.session.scalars(select(ModelTaskBinding).where(ModelTaskBinding.tenant_id == tenant))).all()}
        return ConfigView(providers=PROVIDERS, connections=[self.connection_view(row) for row in connections], tasks=TASKS, bindings=[self.binding_view(task.id, bindings.get(task.id)) for task in TASKS])

    def connection_view(self, row: ModelConnection) -> ConnectionView:
        provider = PROVIDER_MAP.get(row.provider)
        return ConnectionView(id=row.id, name=row.name, provider=row.provider, provider_label=provider.label if provider else "OpenAI 兼容 API", capabilities=provider.capabilities if provider else ["embedding", "chat"], base_url=row.base_url, is_local=row.is_local, api_key_configured=bool(row.api_key_ciphertext), models=row.models or [], status=row.status, status_message=row.status_message, last_checked_at=row.last_checked_at)

    def resolve_provider(self, data: ConnectionInput) -> tuple[str, bool]:
        provider = PROVIDER_MAP[data.provider]
        raw_url = provider.default_base_url if not provider.base_url_editable else data.base_url
        if not raw_url:
            raise AppError(ErrorCode.DATA_INVALID, "该服务提供方必须填写 API 地址")
        base_url, local = normalize_url(raw_url)
        if data.provider == "local_openai" and not local:
            raise AppError(ErrorCode.DATA_INVALID, "本地模型服务必须使用本机或私网地址")
        return base_url, local

    def binding_view(self, task: str, row: ModelTaskBinding | None) -> BindingView:
        if row:
            return BindingView(task=task, connection_id=row.connection_id, model_id=row.model_id, dimension=row.dimension, temperature=row.temperature, test_status=row.test_status, test_message=row.test_message, last_tested_at=row.last_tested_at)
        if task == "review_analysis":
            return BindingView(task=task, fallback={"source": "worker environment", "note": "未绑定时使用审核 worker 的现有配置；绑定后仅使用所选连接，不回退到其他模型。"})
        embedding = task == "tlr_embedding"
        return BindingView(task=task, fallback={"source": ".env", "base_url": self.settings.embedding_base_url if embedding else self.settings.llm_base_url, "model_id": self.settings.embedding_model if embedding else self.settings.llm_model, "api_key_configured": bool(self.settings.embedding_api_key if embedding else self.settings.llm_api_key)})

    async def create(self, tenant: str, data: ConnectionInput) -> ConnectionView:
        base_url, local = self.resolve_provider(data)
        secret = data.api_key.get_secret_value().strip() if data.api_key else ""
        if not local and not secret:
            raise AppError(ErrorCode.LLM_CONFIG_ERROR, "远程模型连接必须填写 API 密钥")
        row = ModelConnection(tenant_id=tenant, name=data.name.strip(), provider=data.provider, base_url=base_url, is_local=local, api_key_ciphertext=self.vault.encrypt(secret))
        self.session.add(row)
        await self.session.commit(); await self.session.refresh(row)
        await self.refresh(row)
        return self.connection_view(row)

    async def update(self, tenant: str, connection_id: str, data: ConnectionInput) -> ConnectionView:
        row = await self._connection(tenant, connection_id)
        row.base_url, row.is_local = self.resolve_provider(data); row.name = data.name.strip(); row.provider = data.provider
        if data.api_key is not None and data.api_key.get_secret_value().strip():
            row.api_key_ciphertext = self.vault.encrypt(data.api_key.get_secret_value().strip())
        if not row.is_local and not row.api_key_ciphertext:
            raise AppError(ErrorCode.LLM_CONFIG_ERROR, "远程模型连接必须填写 API 密钥")
        row.status = "unchecked"; row.models = []
        await self.session.commit(); await self.refresh(row)
        return self.connection_view(row)

    async def refresh_by_id(self, tenant: str, connection_id: str) -> ConnectionView:
        row = await self._connection(tenant, connection_id); await self.refresh(row); return self.connection_view(row)

    async def refresh(self, row: ModelConnection) -> None:
        key = self.vault.decrypt(row.api_key_ciphertext) or ("local" if row.is_local else "")
        try:
            async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds, follow_redirects=False, trust_env=not row.is_local) as client:
                response = await client.get(f"{row.base_url}/models", headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status(); payload = response.json()
                items = payload.get("data")
                if not isinstance(items, list): raise ValueError("data is not a list")
                previous = {item.get("id"): item for item in (row.models or []) if isinstance(item, dict)}
                models = []
                for item in items:
                    if not isinstance(item, dict) or not item.get("id"): continue
                    model_id = str(item["id"])
                    metadata = {"capabilities": ["chat"], "capability_source": "provider", "verification": {}} if row.provider == "deepseek" else {"capabilities": ["unknown"], "capability_source": "unknown", "verification": {}}
                    if row.provider == "local_openai":
                        try:
                            detail = await client.post(f"{ollama_native_root(row.base_url)}/api/show", json={"model": model_id, "verbose": False})
                            detail.raise_for_status()
                            metadata = ollama_model_metadata(detail.json())
                        except (httpx.HTTPError, ValueError, TypeError):
                            pass
                    old = previous.get(model_id, {})
                    if old.get("capability_source") == "manual":
                        metadata["capabilities"] = old.get("capabilities", ["unknown"])
                        metadata["capability_source"] = "manual"
                    metadata["verification"] = old.get("verification", {})
                    for field in ("context_window", "max_output_tokens"):
                        value = item.get(field)
                        if isinstance(value, int) and value > 0:
                            metadata[field] = value
                    models.append({"id": model_id, "owned_by": str(item.get("owned_by", "unknown")), **metadata})
            row.models = sorted(models, key=lambda item: item["id"])
            row.status, row.status_message = "available", f"已读取 {len(row.models)} 个模型"
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            row.models = []; row.status = "unavailable"; row.status_message = self._safe_error(exc)
        row.last_checked_at = datetime.now(UTC); await self.session.commit(); await self.session.refresh(row)

    async def bind(self, tenant: str, task: str, data: BindingInput) -> BindingView:
        info = TASK_MAP.get(task)
        if not info: raise AppError(ErrorCode.DATA_INVALID, "未知模型任务")
        connection = await self._connection(tenant, data.connection_id)
        if info.capability not in self.connection_view(connection).capabilities:
            raise AppError(ErrorCode.DATA_INVALID, "该服务提供方不支持此任务所需的模型能力")
        model = next((item for item in (connection.models or []) if item["id"] == data.model_id), None)
        if connection.models and model is None:
            raise AppError(ErrorCode.DATA_INVALID, "所选模型不在当前连接的可用目录中")
        if model and "unknown" not in model.get("capabilities", ["unknown"]) and info.capability not in model.get("capabilities", []):
            raise AppError(ErrorCode.DATA_INVALID, "所选模型不支持该任务需要的能力")
        dimension = data.dimension or (model.get("embedding_dimension") if model else None)
        if info.capability == "embedding" and dimension is None:
            raise AppError(ErrorCode.DATA_INVALID, "向量模型必须填写输出维度")
        key = (tenant, task); row = await self.session.get(ModelTaskBinding, key)
        if row is None:
            row = ModelTaskBinding(tenant_id=tenant, task=task, connection_id=data.connection_id, model_id=data.model_id, dimension=dimension, temperature=data.temperature); self.session.add(row)
        else:
            row.connection_id, row.model_id, row.dimension, row.temperature = data.connection_id, data.model_id, dimension, data.temperature; row.test_status = "untested"; row.test_message = ""
        await self.session.commit(); await self.session.refresh(row); return self.binding_view(task, row)

    async def test(self, tenant: str, task: str) -> BindingView:
        row = await self.session.get(ModelTaskBinding, (tenant, task))
        if row is None: raise AppError(ErrorCode.LLM_CONFIG_ERROR, "该任务尚未选择模型")
        connection = await self._connection(tenant, row.connection_id)
        key = self.vault.decrypt(connection.api_key_ciphertext) or ("local" if connection.is_local else "")
        try:
            if task == "review_analysis":
                await self.refresh(connection)
                if connection.status != "available" or not any(item["id"] == row.model_id for item in connection.models):
                    raise ValueError("Selected review model catalog is unavailable")
                payload = {"model": row.model_id, "messages": [{"role": "user", "content": "准入检查"}]}
                if connection.provider == "deepseek":
                    if row.model_id not in {"deepseek-flash", "deepseek-v4-pro"}:
                        raise ValueError("No verified reference tokenizer for this model generation")
                    from app.modules.model_config.deepseek_reference import count_chat_tokens
                    count_chat_tokens(payload)
                elif connection.is_local:
                    async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
                        response = await client.post(ollama_native_root(connection.base_url) + "/tokenize",
                                                     json=payload, headers={"Authorization": "Bearer " + key})
                        response.raise_for_status()
                        if not isinstance(response.json().get("count"), int):
                            raise ValueError("Local tokenizer returned no count")
                else:
                    raise ValueError("No verified admission tokenizer for this provider")
                message = "模型目录与准入 tokenizer 可用；未执行 Agent，也不代表生成质量已验证"
            elif TASK_MAP[task].capability == "embedding":
                provider = OpenAICompatibleEmbeddingProvider(base_url=connection.base_url, api_key=key, model=row.model_id, timeout_seconds=self.settings.model_timeout_seconds, trust_env=not connection.is_local)
                vectors = await provider.encode_documents(["model configuration test"])
                if not vectors or len(vectors[0]) != row.dimension:
                    raise ValueError("Embedding dimension does not match the task binding")
                message = f"调用成功，向量维度 {len(vectors[0])}"
            else:
                provider = OpenAICompatibleLLMProvider(base_url=connection.base_url, api_key=key, model=row.model_id, timeout_seconds=self.settings.llm_timeout_seconds, trust_env=not connection.is_local)
                await provider.chat_json(system_prompt="仅返回一个合法 JSON 对象。", user_payload={"reply": "ok"}, temperature=row.temperature or 0.0); message = "结构化 JSON 调用成功"
            row.test_status = "passed"
        except Exception as exc:
            row.test_status, message = "failed", self._safe_error(exc)
        capability = TASK_MAP[task].capability
        models = []
        for item in connection.models or []:
            current = dict(item)
            if current.get("id") == row.model_id:
                verification = dict(current.get("verification") or {})
                verification["admission" if task == "review_analysis" else capability] = row.test_status
                current["verification"] = verification
                if row.test_status == "passed" and capability not in current.get("capabilities", []):
                    current["capabilities"] = sorted((set(current.get("capabilities", [])) - {"unknown"}) | {capability})
                    if current.get("capability_source") == "unknown": current["capability_source"] = "probe"
            models.append(current)
        connection.models = models
        row.test_message, row.last_tested_at = message, datetime.now(UTC); await self.session.commit(); await self.session.refresh(row); return self.binding_view(task, row)

    async def update_model_metadata(self, tenant: str, connection_id: str, model_id: str, data: ModelMetadataInput) -> ModelItem:
        connection = await self._connection(tenant, connection_id)
        models = []
        updated = None
        capabilities = sorted(set(data.capabilities) - {"unknown"}) or ["unknown"]
        for item in connection.models or []:
            current = dict(item)
            if current.get("id") == model_id:
                current["capabilities"] = capabilities
                current["capability_source"] = "manual"
                updated = current
            models.append(current)
        if updated is None:
            raise AppError(ErrorCode.DATA_NOT_FOUND, "模型不在当前连接的可用目录中")
        connection.models = models
        await self.session.commit()
        return ModelItem.model_validate(updated)

    async def unbind(self, tenant: str, task: str) -> BindingView:
        if task not in TASK_MAP:
            raise AppError(ErrorCode.DATA_INVALID, "未知模型任务")
        row = await self.session.get(ModelTaskBinding, (tenant, task))
        if row is not None:
            await self.session.delete(row)
            await self.session.commit()
        return self.binding_view(task, None)

    async def delete(self, tenant: str, connection_id: str) -> None:
        row = await self._connection(tenant, connection_id)
        bindings = list(
            (
                await self.session.scalars(
                    select(ModelTaskBinding).where(
                        ModelTaskBinding.tenant_id == tenant,
                        ModelTaskBinding.connection_id == connection_id,
                    )
                )
            ).all()
        )
        for binding in bindings:
            await self.session.delete(binding)
        await self.session.delete(row)
        await self.session.commit()

    async def _connection(self, tenant: str, connection_id: str) -> ModelConnection:
        row = await self.session.get(ModelConnection, connection_id)
        if row is None or row.tenant_id != tenant: raise AppError(ErrorCode.DATA_NOT_FOUND, "模型连接不存在")
        return row

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError): return f"服务返回 HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.TimeoutException): return "连接超时"
        if isinstance(exc, httpx.HTTPError): return "无法连接模型服务"
        if isinstance(exc, AppError):
            if isinstance(exc.data, dict) and isinstance(exc.data.get("reason"), str):
                return exc.data["reason"][:300]
            return exc.message
        return "响应格式或模型输出不符合要求"
