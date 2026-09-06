from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    SessionDep,
    SettingsDep,
    get_embedding_provider,
    get_llm_provider,
)
from app.modules.tlr.models import TlrArtifact, TlrCandidate, TlrElement, TlrEvaluation, TlrLink
from app.modules.tlr.repository import TlrRepository
from app.modules.tlr.schemas import (
    ArtifactView,
    CandidateView,
    DatasetInput,
    DatasetView,
    ElementView,
    EvaluationInput,
    EvaluationView,
    LinkView,
    Page,
    RunInput,
    RunView,
)
from app.modules.tlr.service import TlrService
from app.providers.embedding.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/tlr", tags=["tlr"])
Scope = Annotated[str, Query(min_length=1, max_length=128)]


def get_service(
    session: SessionDep,
    settings: SettingsDep,
    embedding: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
):
    return TlrService(session, embedding, llm, settings)


Service = Annotated[TlrService, Depends(get_service)]


@router.post("/datasets", response_model=ApiResponse[DatasetView])
async def import_dataset(request: DatasetInput, service: Service):
    return ApiResponse(data=DatasetView.model_validate(await service.import_dataset(request)))


@router.get("/datasets/{dataset_id}", response_model=ApiResponse[DatasetView])
async def get_dataset(dataset_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep):
    dataset = await TlrRepository(session).dataset(dataset_id, tenant_id, project_id)
    return ApiResponse(data=DatasetView.model_validate(dataset))


@router.get("/datasets/{dataset_id}/artifacts", response_model=ApiResponse[Page])
async def get_artifacts(
    dataset_id: str,
    tenant_id: Scope,
    project_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    repo = TlrRepository(session)
    await repo.dataset(dataset_id, tenant_id, project_id)
    return ApiResponse(
        data=await repo.page(
            TlrArtifact, TlrArtifact.dataset_id, dataset_id, ArtifactView, offset, limit
        )
    )


@router.post("/runs", response_model=ApiResponse[RunView])
async def create_run(request: RunInput, service: Service):
    return ApiResponse(data=RunView.model_validate(await service.create_run(request)))


@router.post("/runs/{run_id}/execute", response_model=ApiResponse[RunView])
async def execute_run(run_id: str, tenant_id: Scope, project_id: Scope, service: Service):
    return ApiResponse(
        data=RunView.model_validate(await service.execute(run_id, tenant_id, project_id))
    )


@router.get("/runs/{run_id}", response_model=ApiResponse[RunView])
async def get_run(run_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep):
    return ApiResponse(
        data=RunView.model_validate(await TlrRepository(session).run(run_id, tenant_id, project_id))
    )


@router.get("/runs/{run_id}/outputs/{kind}", response_model=ApiResponse[Page])
async def get_outputs(
    run_id: str,
    kind: Literal["elements", "candidates", "links", "evaluations"],
    tenant_id: Scope,
    project_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    repo = TlrRepository(session)
    await repo.run(run_id, tenant_id, project_id)
    model, schema = {
        "elements": (TlrElement, ElementView),
        "candidates": (TlrCandidate, CandidateView),
        "links": (TlrLink, LinkView),
        "evaluations": (TlrEvaluation, EvaluationView),
    }[kind]
    return ApiResponse(data=await repo.page(model, model.run_id, run_id, schema, offset, limit))


@router.post("/runs/{run_id}/evaluations", response_model=ApiResponse[EvaluationView])
async def evaluate(
    run_id: str, tenant_id: Scope, project_id: Scope, request: EvaluationInput, service: Service
):
    return ApiResponse(
        data=EvaluationView.model_validate(
            await service.evaluate(run_id, tenant_id, project_id, request)
        )
    )
