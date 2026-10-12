"""Global console reads and a credential-isolated model transport gateway.

No analysis, second artifact store, or legacy TLR is implemented here.
"""
from urllib.parse import quote

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from app.api.dependencies import SessionDep, SettingsDep
from app.db.base import Base
from app.modules.artifacts.models import Artifact, Project
from app.modules.model_config.models import ModelConnection, ModelTaskBinding
from app.modules.model_config.service import TASK_MAP, normalize_url
from app.modules.model_config.vault import CredentialVault
from app.modules.traceability.models import TlrRun, TraceLink

router = APIRouter(tags=["global-console"])


async def model_transport(session, settings, tenant: str, task: str):
    if task not in TASK_MAP:
        raise HTTPException(404, "Unknown model task")
    binding = await session.get(ModelTaskBinding, (tenant, task))
    if binding:
        connection = await session.get(ModelConnection, binding.connection_id)
        if connection is None or connection.tenant_id != tenant:
            raise HTTPException(422, "Invalid model connection scope")
        return connection.base_url, CredentialVault(settings).decrypt(connection.api_key_ciphertext) or "local", binding.model_id, connection.is_local
    if not settings.llm_enabled:
        raise HTTPException(503, "No enabled model or explicit task binding")
    base, local = normalize_url(settings.llm_base_url)
    return base, settings.llm_api_key or "local", settings.llm_model, local


@router.get("/model-config/tasks/{task}/runtime")
async def task_runtime(task: str, tenant_id: str, session: SessionDep, settings: SettingsDep):
    binding = await session.get(ModelTaskBinding, (tenant_id, task))
    if task == "review_analysis" and binding is None:
        # This task previously lived exclusively in the SDK worker environment.
        # Absence is explicit; a BROKEN binding must never use that environment.
        return {"configured": False, "source": "worker_environment"}
    _, _, model, _ = await model_transport(session, settings, tenant_id, task)
    result = {"configured": True, "model": model,
              "transport": f"/api/v1/model-config/inference/{quote(tenant_id, safe='')}/{task}"}
    if binding:
        connection = await session.get(ModelConnection, binding.connection_id)
        metadata = next((item for item in (connection.models or []) if item.get("id") == model), {})
        result.update(connection_id=connection.id, provider=connection.provider,
                      is_local=connection.is_local, context_window=metadata.get("context_window"),
                      max_output_tokens=metadata.get("max_output_tokens"),
                      thinking_mode="disabled" if connection.provider == "deepseek" else "provider_default",
                      tool_validation="SDK and canonical workspace guards")
    return result


@router.post("/model-config/inference/{tenant_id}/{task}/tokenize")
async def tokenize_transport(tenant_id: str, task: str, request: Request,
                             session: SessionDep, settings: SettingsDep):
    if task != "review_analysis":
        raise HTTPException(422, "Only review analysis exposes admission counting")
    binding = await session.get(ModelTaskBinding, (tenant_id, task))
    if binding is None:
        raise HTTPException(503, "Review model task is not bound")
    base, key, model, local = await model_transport(session, settings, tenant_id, task)
    connection = await session.get(ModelConnection, binding.connection_id)
    body = await request.json()
    if not isinstance(body, dict) or body.get("model") != model:
        raise HTTPException(409, "Review model binding changed; resolve the runtime again")
    try:
        if connection.provider == "deepseek":
            if model not in {"deepseek-flash", "deepseek-v4-pro"}:
                raise HTTPException(422, "No verified offline tokenizer for this DeepSeek model generation")
            # Authentication/availability is observed, not inferred from a saved
            # UI label. No generative probe or hidden retry is made here.
            async with httpx.AsyncClient(timeout=4.0, trust_env=not local) as client:
                response = await client.get(base.rstrip("/") + "/models",
                                            headers={"Authorization": "Bearer " + key})
                if response.is_error:
                    return Response(response.content, status_code=response.status_code,
                                    media_type="application/json")
                items = response.json().get("data", [])
                metadata = next((item for item in items if item.get("id") == model), None)
                if metadata is None:
                    raise HTTPException(503, "Bound model is absent from the provider catalog")
            from app.modules.model_config.deepseek_reference import REVISION, count_chat_tokens
            count = await anyio.to_thread.run_sync(count_chat_tokens, body)
            return {"count": count, "max_model_len": metadata.get("context_window"),
                    "count_source": "deepseek_v4_official_reference_estimate",
                    "tokenizer_revision": REVISION,
                    "billing_count_authority": "provider_response.usage"}
        if local:
            root = base[:-3].rstrip("/") if base.endswith("/v1") else base.rstrip("/")
            async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
                response = await client.post(root + "/tokenize", json=body,
                                             headers={"Authorization": "Bearer " + key})
            return Response(response.content, status_code=response.status_code,
                            media_type="application/json")
        raise HTTPException(422, "No verified admission tokenizer for the selected provider")
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Model admission health request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Model admission provider is unavailable") from exc
    except (ValueError, TypeError, KeyError, AssertionError) as exc:
        raise HTTPException(422, "Model admission could not encode this request") from exc


@router.post("/model-config/inference/{tenant_id}/{task}/chat/completions")
async def chat_transport(tenant_id: str, task: str, request: Request, session: SessionDep, settings: SettingsDep):
    if task not in {"agent_chat", "peer_llm", "review_analysis"}:
        raise HTTPException(422, "Task does not expose a chat gateway")
    base, key, model, local = await model_transport(session, settings, tenant_id, task)
    body = await request.json()
    if not isinstance(body, dict) or body.get("stream"):
        raise HTTPException(422, "Only non-streaming chat requests are supported")
    if task == "review_analysis":
        if body.get("model") != model:
            raise HTTPException(409, "Review model binding changed; request not sent")
        binding = await session.get(ModelTaskBinding, (tenant_id, task))
        if binding is None:
            raise HTTPException(503, "Review model task is not bound")
        connection = await session.get(ModelConnection, binding.connection_id)
        if connection.provider == "deepseek":
            body.pop("chat_template_kwargs", None)  # vLLM-only, not a DeepSeek API field
            body["thinking"] = {"type": "disabled"}
            # Standard documented function calling, not the beta strict-schema
            # subset (which excludes minLength/maxItems used by SDK tools).
            # Keep the schemas and ALL SDK/backend argument/evidence validation.
            for tool in body.get("tools", []):
                if tool.get("type") == "function":
                    tool["function"].pop("strict", None)
    body["model"] = model
    timeout = settings.review_model_timeout_seconds if task == "review_analysis" else settings.llm_timeout_seconds
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=not local) as client:
            result = await client.post(base.rstrip("/") + "/chat/completions", json=body, headers={"Authorization": "Bearer " + key})
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Model provider timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Model provider unavailable") from exc
    # No provider credentials or arbitrary response headers are exposed.
    return Response(result.content, status_code=result.status_code, media_type="application/json")


@router.get("/console/schema")
async def console_schema():
    return {"tables": {table.name: list(table.columns.keys()) for table in Base.metadata.sorted_tables}}


@router.get("/console/projects")
async def projects(tenant_id: str, session: SessionDep):
    items = []
    for project in await session.scalars(select(Project).where(Project.tenant_id == tenant_id).order_by(Project.created_at.desc())):
        artifacts = await session.scalar(select(func.count()).select_from(Artifact).where(Artifact.project_id == project.id))
        links = await session.scalar(select(func.count()).select_from(TraceLink).join(TlrRun, TlrRun.id == TraceLink.run_id).where(TlrRun.project_id == project.id, TlrRun.tenant_id == tenant_id, TlrRun.status == "completed"))
        items.append({"id": project.id, "name": project.name, "description": project.description, "status": "active", "artifact_count": artifacts, "trace_link_count": links})
    return items
