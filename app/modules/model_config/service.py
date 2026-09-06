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
from app.modules.model_config.schemas import BindingInput, BindingView, ConfigView, ConnectionInput, ConnectionView, TaskInfo
from app.modules.model_config.vault import CredentialVault
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


TASKS = [
    TaskInfo(id="tlr_embedding", label="TLR 向量化", description="为分割后的制品单元生成检索向量。", capability="embedding"),
    TaskInfo(id="tlr_classification", label="TLR 链接判定", description="对候选制品对进行结构化相关性判断。", capability="chat"),
    TaskInfo(id="architecture_extraction", label="架构结构抽取", description="将架构文档抽取为可追踪的结构单元。", capability="chat"),
]
TASK_MAP = {task.id: task for task in TASKS}


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


class ModelConfigService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session, self.settings, self.vault = session, settings, CredentialVault(settings)

    async def view(self, tenant: str) -> ConfigView:
        connections = list((await self.session.scalars(select(ModelConnection).where(ModelConnection.tenant_id == tenant).order_by(ModelConnection.name))).all())
        bindings = {row.task: row for row in (await self.session.scalars(select(ModelTaskBinding).where(ModelTaskBinding.tenant_id == tenant))).all()}
        return ConfigView(connections=[self.connection_view(row) for row in connections], tasks=TASKS, bindings=[self.binding_view(task.id, bindings.get(task.id)) for task in TASKS])

    def connection_view(self, row: ModelConnection) -> ConnectionView:
        return ConnectionView(id=row.id, name=row.name, provider=row.provider, base_url=row.base_url, is_local=row.is_local, api_key_configured=bool(row.api_key_ciphertext), models=row.models or [], status=row.status, status_message=row.status_message, last_checked_at=row.last_checked_at)

    def binding_view(self, task: str, row: ModelTaskBinding | None) -> BindingView:
        if row:
            return BindingView(task=task, connection_id=row.connection_id, model_id=row.model_id, dimension=row.dimension, test_status=row.test_status, test_message=row.test_message, last_tested_at=row.last_tested_at)
        embedding = task == "tlr_embedding"
        return BindingView(task=task, fallback={"source": ".env", "base_url": self.settings.model_base_url if embedding else self.settings.llm_base_url, "model_id": self.settings.embedding_model if embedding else self.settings.llm_model, "api_key_configured": bool(self.settings.model_api_key if embedding else self.settings.llm_api_key)})

    async def create(self, tenant: str, data: ConnectionInput) -> ConnectionView:
        base_url, local = normalize_url(data.base_url)
        secret = data.api_key.get_secret_value().strip() if data.api_key else ""
        if not local and not secret:
            raise AppError(ErrorCode.LLM_CONFIG_ERROR, "远程模型连接必须填写 API 密钥")
        row = ModelConnection(tenant_id=tenant, name=data.name.strip(), base_url=base_url, is_local=local, api_key_ciphertext=self.vault.encrypt(secret))
        self.session.add(row)
        await self.session.commit(); await self.session.refresh(row)
        await self.refresh(row)
        return self.connection_view(row)

    async def update(self, tenant: str, connection_id: str, data: ConnectionInput) -> ConnectionView:
        row = await self._connection(tenant, connection_id)
        row.base_url, row.is_local = normalize_url(data.base_url); row.name = data.name.strip()
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
            row.models = sorted([{"id": str(item["id"]), "owned_by": str(item.get("owned_by", "unknown"))} for item in items if isinstance(item, dict) and item.get("id")], key=lambda item: item["id"])
            row.status, row.status_message = "available", f"已读取 {len(row.models)} 个模型"
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            row.models = []; row.status = "unavailable"; row.status_message = self._safe_error(exc)
        row.last_checked_at = datetime.now(UTC); await self.session.commit(); await self.session.refresh(row)

    async def bind(self, tenant: str, task: str, data: BindingInput) -> BindingView:
        info = TASK_MAP.get(task)
        if not info: raise AppError(ErrorCode.DATA_INVALID, "未知模型任务")
        connection = await self._connection(tenant, data.connection_id)
        if connection.models and data.model_id not in {item["id"] for item in connection.models}:
            raise AppError(ErrorCode.DATA_INVALID, "所选模型不在当前连接的可用目录中")
        if info.capability == "embedding" and data.dimension is None:
            raise AppError(ErrorCode.DATA_INVALID, "向量模型必须填写输出维度")
        key = (tenant, task); row = await self.session.get(ModelTaskBinding, key)
        if row is None:
            row = ModelTaskBinding(tenant_id=tenant, task=task, connection_id=data.connection_id, model_id=data.model_id, dimension=data.dimension); self.session.add(row)
        else:
            row.connection_id, row.model_id, row.dimension = data.connection_id, data.model_id, data.dimension; row.test_status = "untested"; row.test_message = ""
        await self.session.commit(); await self.session.refresh(row); return self.binding_view(task, row)

    async def test(self, tenant: str, task: str) -> BindingView:
        row = await self.session.get(ModelTaskBinding, (tenant, task))
        if row is None: raise AppError(ErrorCode.LLM_CONFIG_ERROR, "该任务尚未选择模型")
        connection = await self._connection(tenant, row.connection_id)
        key = self.vault.decrypt(connection.api_key_ciphertext) or ("local" if connection.is_local else "")
        try:
            if TASK_MAP[task].capability == "embedding":
                provider = OpenAICompatibleEmbeddingProvider(base_url=connection.base_url, api_key=key, model=row.model_id, expected_dimension=row.dimension or 1, timeout_seconds=self.settings.model_timeout_seconds, trust_env=not connection.is_local)
                vectors = await provider.embed(["model configuration test"]); message = f"调用成功，向量维度 {len(vectors[0])}"
            else:
                provider = OpenAICompatibleLLMProvider(base_url=connection.base_url, api_key=key, model=row.model_id, timeout_seconds=self.settings.llm_timeout_seconds, trust_env=not connection.is_local)
                await provider.chat_json(system_prompt="Return one JSON object only.", user_payload={"reply": "ok"}); message = "结构化 JSON 调用成功"
            row.test_status = "passed"
        except Exception as exc:
            row.test_status, message = "failed", self._safe_error(exc)
        row.test_message, row.last_tested_at = message, datetime.now(UTC); await self.session.commit(); await self.session.refresh(row); return self.binding_view(task, row)

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
        if isinstance(exc, AppError): return exc.message
        return "响应格式或模型输出不符合要求"
