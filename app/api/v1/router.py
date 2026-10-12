from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.api.dependencies import (
    SessionDep,
    get_embedding_provider,
    get_llm_provider,
)
from app.core.config import get_settings
from app.modules.model_config.selection import select_tlr_models
from app.modules.traceability.models import (
    Candidate,
    RetrievalProjection,
    SemanticAnnotation,
    TlrRun,
    TraceLink,
    TraceUnit,
)
from app.modules.traceability.schemas import (
    CandidateView,
    RetrievalProjectionView,
    RunCreate,
    RunView,
    SemanticAnnotationView,
    TraceLinkView,
    TraceUnitView,
)
from app.modules.traceability.service import TraceabilityService
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider

router = APIRouter(prefix="/traceability", tags=["traceability"])


@router.post("/runs", response_model=RunView)
async def create_run(request: RunCreate, session: SessionDep):
    return await TraceabilityService(session).create_run(request)


@router.post("/runs/{run_id}/execute", response_model=RunView)
async def execute_run(
    run_id: str,
    request: Request,
    session: SessionDep,
    llm: Annotated[LLMProvider | None, Depends(get_llm_provider)],
    embedding: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    tenant_id: str,
    project_id: str,
):
    run = await session.get(TlrRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if (run.tenant_id, run.project_id) != (tenant_id, project_id):
        raise HTTPException(404, "run not found in project scope")
    llm, embedding = await select_tlr_models(session, get_settings(), run.tenant_id, llm, embedding)
    from app.modules.traceability.execution_control import run_owned_tlr
    try:
        return await run_owned_tlr(request, run_id, TraceabilityService(session, llm, embedding).execute(run_id),
                                   idle_seconds=get_settings().tlr_no_progress_seconds)
    except TimeoutError as exc:
        await session.rollback()
        row = await session.get(TlrRun, run_id)
        row.status, row.error = "interrupted", str(exc)
        await session.commit()
        raise HTTPException(408, str(exc)) from exc


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, session: SessionDep):
    row = await session.get(TlrRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@router.get("/runs/{run_id}/units", response_model=list[TraceUnitView])
async def list_units(run_id: str, session: SessionDep):
    return list(
        await session.scalars(
            select(TraceUnit)
            .where(TraceUnit.run_id == run_id)
            .order_by(TraceUnit.side, TraceUnit.ordinal)
        )
    )


@router.get("/runs/{run_id}/annotations", response_model=list[SemanticAnnotationView])
async def list_annotations(run_id: str, session: SessionDep):
    return list(
        await session.scalars(
            select(SemanticAnnotation)
            .where(SemanticAnnotation.run_id == run_id)
            .order_by(SemanticAnnotation.node_id)
        )
    )


@router.get("/runs/{run_id}/projections", response_model=list[RetrievalProjectionView])
async def list_projections(run_id: str, session: SessionDep):
    return list(
        await session.scalars(
            select(RetrievalProjection)
            .where(RetrievalProjection.run_id == run_id)
            .order_by(RetrievalProjection.node_id, RetrievalProjection.channel)
        )
    )


@router.get("/runs/{run_id}/candidates", response_model=list[CandidateView])
async def list_candidates(run_id: str, session: SessionDep):
    return list(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id == run_id)
            .order_by(Candidate.source_unit_id, Candidate.fused_rank, Candidate.id)
        )
    )


@router.get("/runs/{run_id}/links", response_model=list[TraceLinkView])
async def list_links(run_id: str, session: SessionDep):
    return list(
        await session.scalars(
            select(TraceLink)
            .join(TlrRun, TlrRun.id == TraceLink.run_id)
            .where(TraceLink.run_id == run_id, TlrRun.status == "completed")
        )
    )


@router.get("/runs/{run_id}/diagnostics")
async def retrieval_diagnostics(run_id: str, source_node_id: str, session: SessionDep):
    source_annotation = await session.scalar(
        select(SemanticAnnotation).where(
            SemanticAnnotation.run_id == run_id,
            SemanticAnnotation.node_id == source_node_id,
        )
    )
    if source_annotation is None:
        raise HTTPException(status_code=404, detail="source annotation not found")
    source_unit = await session.scalar(
        select(TraceUnit).where(
            TraceUnit.run_id == run_id,
            TraceUnit.node_id == source_node_id,
            TraceUnit.side == "source",
        )
    )
    if source_unit is None:
        return {
            "source_node_id": source_node_id,
            "role_filtering": {
                "source_role": source_annotation.role,
                "filtered": True,
                "reason": "context/exclude nodes are retained but do not become retrieval queries",
            },
            "dense_top_k": [],
            "bm25_top_k": [],
            "rrf_order": [],
            "structural_expansion": [],
            "fine_screening_order": [],
            "final_llm_decisions": [],
        }
    rows = list(
        await session.scalars(
            select(Candidate).where(
                Candidate.run_id == run_id,
                Candidate.source_unit_id == source_unit.id,
            )
        )
    )
    target_units = {
        item.id: item
        for item in await session.scalars(
            select(TraceUnit).where(TraceUnit.id.in_([row.target_unit_id for row in rows]))
        )
    }

    def view(row: Candidate) -> dict:
        return {
            "candidate_id": row.id,
            "target_node_id": target_units[row.target_unit_id].node_id,
            "heading_path": row.diagnostics.get("heading_path", []),
            "node_type": row.diagnostics.get("node_type"),
            "preview": row.diagnostics.get("preview"),
            "dense_score": row.dense_score,
            "dense_rank": row.dense_rank,
            "bm25_score": row.bm25_score,
            "bm25_rank": row.bm25_rank,
            "synopsis_dense_score": row.synopsis_dense_score,
            "synopsis_dense_rank": row.synopsis_dense_rank,
            "rrf_score": row.rrf_score,
            "fused_rank": row.fused_rank,
            "retrieval_sources": row.retrieval_sources,
            "structural_reasons": row.structural_reasons,
            "reranker_score": row.reranker_score,
            "reranker_rank": row.reranker_rank,
            "fine_order": row.diagnostics.get("fine_order"),
            "final_relation": row.final_relation,
        }

    return {
        "source_node_id": source_node_id,
        "role_filtering": {"source_role": source_annotation.role, "filtered": False},
        "dense_top_k": [
            view(row)
            for row in sorted((r for r in rows if r.dense_rank), key=lambda r: r.dense_rank)
        ],
        "bm25_top_k": [
            view(row) for row in sorted((r for r in rows if r.bm25_rank), key=lambda r: r.bm25_rank)
        ],
        "rrf_order": [
            view(row)
            for row in sorted(
                (r for r in rows if r.fused_rank is not None), key=lambda r: r.fused_rank
            )
        ],
        "structural_expansion": [view(row) for row in rows if row.structural_reasons],
        "fine_screening_order": [
            view(row)
            for row in sorted(
                rows, key=lambda r: int(r.diagnostics.get("fine_order", 10**9))
            )
        ],
        "final_llm_decisions": [view(row) for row in rows if row.final_relation],
    }
