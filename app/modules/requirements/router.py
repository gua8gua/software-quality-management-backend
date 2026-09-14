from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import SessionDep, SettingsDep, get_llm_provider
from app.modules.requirements.models import RequirementAnalysis
from app.modules.requirements.schemas import BuildInput
from app.modules.requirements.service import RequirementService
from app.modules.tlr.repository import TlrRepository
from app.providers.llm.base import LLMProvider
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/requirements", tags=["requirements"])
Scope = Annotated[str, Query(min_length=1, max_length=128)]


@router.post("/analyses", response_model=ApiResponse[dict])
async def build(
    request: BuildInput,
    session: SessionDep,
    settings: SettingsDep,
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
):
    return ApiResponse(data=await RequirementService(session, settings, llm).build(request))


@router.get("/analyses/{identifier}", response_model=ApiResponse[dict])
async def get(identifier: str, tenant_id: Scope, project_id: Scope, session: SessionDep):
    row = await TlrRepository(session).scoped(
        RequirementAnalysis, identifier, tenant_id, project_id
    )
    return ApiResponse(data=RequirementService.view(row))
