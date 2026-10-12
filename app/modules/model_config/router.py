# ruff: noqa: E501, I001
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import SessionDep, SettingsDep
from app.modules.model_config.schemas import BindingInput, BindingView, ConfigView, ConnectionInput, ConnectionView, ModelItem, ModelMetadataInput
from app.modules.model_config.service import ModelConfigService
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/model-config", tags=["model-config"])
Tenant = Annotated[str, Query(min_length=1, max_length=128)]


@router.get("", response_model=ApiResponse[ConfigView])
async def get_config(tenant_id: Tenant, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).view(tenant_id))


@router.post("/connections", response_model=ApiResponse[ConnectionView])
async def create_connection(tenant_id: Tenant, request: ConnectionInput, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).create(tenant_id, request))


@router.put("/connections/{connection_id}", response_model=ApiResponse[ConnectionView])
async def update_connection(connection_id: str, tenant_id: Tenant, request: ConnectionInput, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).update(tenant_id, connection_id, request))


@router.post("/connections/{connection_id}/refresh", response_model=ApiResponse[ConnectionView])
async def refresh_connection(connection_id: str, tenant_id: Tenant, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).refresh_by_id(tenant_id, connection_id))


@router.put("/connections/{connection_id}/models/{model_id:path}", response_model=ApiResponse[ModelItem])
async def update_model_metadata(connection_id: str, model_id: str, tenant_id: Tenant, request: ModelMetadataInput, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).update_model_metadata(tenant_id, connection_id, model_id, request))


@router.delete("/connections/{connection_id}", response_model=ApiResponse[bool])
async def delete_connection(connection_id: str, tenant_id: Tenant, session: SessionDep, settings: SettingsDep):
    await ModelConfigService(session, settings).delete(tenant_id, connection_id)
    return ApiResponse(data=True)


@router.put("/tasks/{task}", response_model=ApiResponse[BindingView])
async def bind_task(task: str, tenant_id: Tenant, request: BindingInput, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).bind(tenant_id, task, request))


@router.post("/tasks/{task}/test", response_model=ApiResponse[BindingView])
async def test_task(task: str, tenant_id: Tenant, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).test(tenant_id, task))


@router.delete("/tasks/{task}", response_model=ApiResponse[BindingView])
async def unbind_task(task: str, tenant_id: Tenant, session: SessionDep, settings: SettingsDep):
    return ApiResponse(data=await ModelConfigService(session, settings).unbind(tenant_id, task))
