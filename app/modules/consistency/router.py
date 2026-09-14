from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.dependencies import SessionDep, SettingsDep, get_llm_provider
from app.modules.consistency.models import ConsistencyRun
from app.modules.consistency.schemas import AnalysisInput, ReviewInput
from app.modules.consistency.service import ConsistencyService
from app.modules.tlr.models import TlrRun
from app.providers.llm.base import LLMProvider
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/consistency", tags=["consistency"])
Scope = Annotated[str, Query(min_length=1, max_length=128)]


def get_service(
    session: SessionDep,
    settings: SettingsDep,
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
):
    return ConsistencyService(session, settings, llm)


Service = Annotated[ConsistencyService, Depends(get_service)]


@router.get("/runs", response_model=ApiResponse[dict])
async def list_runs(
    tenant_id: Scope,
    project_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    filters = (
        ConsistencyRun.tenant_id == tenant_id,
        ConsistencyRun.project_id == project_id,
        TlrRun.tenant_id == tenant_id,
        TlrRun.project_id == project_id,
    )
    query = (
        select(
            ConsistencyRun.id,
            ConsistencyRun.status,
            ConsistencyRun.tlr_run_id,
            ConsistencyRun.created_at,
            ConsistencyRun.request,
            TlrRun.dataset_id,
        )
        .join(TlrRun, TlrRun.id == ConsistencyRun.tlr_run_id)
        .where(*filters)
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        (
            await session.execute(
                query.order_by(ConsistencyRun.created_at.desc(), ConsistencyRun.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    return ApiResponse(
        data={
            "items": [
                {
                    **{k: v for k, v in row.items() if k != "request"},
                    "analysis_kind": row["request"].get("analysis_kind", "code"),
                    "document_relation": row["request"].get("document_relation", "refinement"),
                }
                for row in rows
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
    )


@router.post("/runs", response_model=ApiResponse[dict])
async def create(request: AnalysisInput, service: Service):
    """Prepare an inspectable evidence snapshot without calling the model."""
    return ApiResponse(data=await service.view(await service.create(request)))


@router.get("/runs/{run_id}", response_model=ApiResponse[dict])
async def get(run_id: str, tenant_id: Scope, project_id: Scope, service: Service):
    return ApiResponse(data=await service.view(await service.get(run_id, tenant_id, project_id)))


@router.post("/runs/{run_id}/execute", response_model=ApiResponse[dict])
async def execute(run_id: str, tenant_id: Scope, project_id: Scope, service: Service):
    """Execute or retry failed requirements; successful results remain unchanged."""
    return ApiResponse(
        data=await service.view(await service.execute(run_id, tenant_id, project_id))
    )


@router.post("/runs/{run_id}/reviews", response_model=ApiResponse[dict])
async def review(run_id: str, request: ReviewInput, service: Service):
    """Append review evidence without overwriting implementation records."""
    return ApiResponse(data=await service.review(run_id, request))
