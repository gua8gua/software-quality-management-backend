"""Explicit, read-only HTTP surfaces mirroring the information tool contracts."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import SessionDep, get_embedding_provider
from app.modules.information_access.review_models import Review
from app.modules.information_access.navigation import StructureNavigation
from app.modules.information_access.service import InformationTools
from app.modules.information_access.visualization import tlr_scope, tlr_visualization
from app.providers.embeddings.base import EmbeddingProvider

router = APIRouter(prefix="/projects/{project_id}/information", tags=["information-tools"])


def access(session: SessionDep, project_id: str, tenant_id: str) -> InformationTools:
    return InformationTools(session, project_id, tenant_id)


@router.get("/artifacts/{artifact_id}/navigation/resolve")
async def resolve_position(project_id: str, artifact_id: str, session: SessionDep,
                           position: str = "/", tenant_id: str = "local"):
    return await StructureNavigation(session, project_id, tenant_id).resolve(artifact_id, position)


@router.get("/artifacts/{artifact_id}/navigation")
async def browse_structure(project_id: str, artifact_id: str, session: SessionDep,
                           position: str = "/", offset: int = 0, limit: int = 50,
                           tenant_id: str = "local"):
    return await StructureNavigation(session, project_id, tenant_id).browse(
        artifact_id, position, offset=offset, limit=limit)


@router.get("/nodes/{node_id}/navigation-position")
async def node_position(project_id: str, node_id: str, session: SessionDep,
                        tenant_id: str = "local"):
    return await StructureNavigation(session, project_id, tenant_id).locate(node_id)


@router.get("/artifacts")
async def list_artifacts(project_id: str, session: SessionDep, tenant_id: str = "local",
                         offset: int = 0, limit: int = 30):
    return await access(session, project_id, tenant_id).list_artifacts(offset=offset, limit=limit)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(project_id: str, artifact_id: str, session: SessionDep,
                       tenant_id: str = "local"):
    return await access(session, project_id, tenant_id).get_artifact(artifact_id)


@router.get("/artifacts/{artifact_id}/status")
async def artifact_status(project_id: str, artifact_id: str, session: SessionDep,
                          tenant_id: str = "local", offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).artifact_status(
        artifact_id, offset=offset, limit=limit)


@router.get("/artifacts/{artifact_id}/relations")
async def artifact_relations(project_id: str, artifact_id: str, session: SessionDep,
                             tenant_id: str = "local", run_id: str | None = None,
                             offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).artifact_relations(
        artifact_id, run_id=run_id, offset=offset, limit=limit)


@router.get("/artifacts/{artifact_id}/outline")
async def document_outline(project_id: str, artifact_id: str, session: SessionDep,
                           tenant_id: str = "local", offset: int = 0,
                           limit: int = 50, max_depth: int = 8):
    return await access(session, project_id, tenant_id).document_outline(
        artifact_id, offset=offset, limit=limit, max_depth=max_depth)


@router.get("/artifacts/{artifact_id}/level/{depth}")
async def tree_level(project_id: str, artifact_id: str, depth: int, session: SessionDep,
                     tenant_id: str = "local", section_id: str | None = None,
                     node_type: str | None = None, offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).tree_level(
        artifact_id, depth, section_id=section_id, node_type=node_type,
        offset=offset, limit=limit)


@router.get("/artifacts/{artifact_id}/read")
async def read_artifact_range(project_id: str, artifact_id: str, session: SessionDep,
                              tenant_id: str = "local", start_order: int = 0,
                              offset: int = 0, limit: int = 30,
                              page_number: int | None = None):
    return await access(session, project_id, tenant_id).read_range(
        artifact_id, start_order=start_order, offset=offset,
        limit=limit, page_number=page_number)


@router.get("/artifacts/{artifact_id}/search")
async def search_artifact(project_id: str, artifact_id: str, query: str,
                          session: SessionDep, tenant_id: str = "local",
                          mode: str = "bm25", node_type: str | None = None,
                          section_id: str | None = None, offset: int = 0,
                          limit: int = 20):
    return await access(session, project_id, tenant_id).search(
        artifact_id, query, mode=mode, node_type=node_type,
        section_id=section_id, offset=offset, limit=limit)


@router.get("/nodes/{node_id}")
async def node_detail(project_id: str, node_id: str, session: SessionDep,
                      tenant_id: str = "local", text_start: int = 0,
                      text_limit: int = 4000, include_structure: bool = False,
                      structure_start: int = 0, structure_limit: int = 8000,
                      metadata_start: int = 0, metadata_limit: int = 8000):
    return await access(session, project_id, tenant_id).node_detail(
        node_id, text_start=text_start, text_limit=text_limit,
        include_structure=include_structure, structure_start=structure_start,
        structure_limit=structure_limit, metadata_start=metadata_start,
        metadata_limit=metadata_limit)


@router.get("/nodes/{node_id}/subtree")
async def subtree(project_id: str, node_id: str, session: SessionDep,
                  tenant_id: str = "local", offset: int = 0,
                  limit: int = 50, max_depth: int = 2):
    return await access(session, project_id, tenant_id).subtree(
        node_id, offset=offset, limit=limit, max_depth=max_depth)


@router.get("/nodes/{node_id}/path")
async def node_path(project_id: str, node_id: str, session: SessionDep,
                    tenant_id: str = "local"):
    return await access(session, project_id, tenant_id).node_path(node_id)


@router.get("/nodes/{node_id}/neighborhood")
async def node_neighborhood(project_id: str, node_id: str, session: SessionDep,
                            tenant_id: str = "local", sibling_limit: int = 4,
                            child_limit: int = 20, sibling_offset: int = 0,
                            child_offset: int = 0):
    return await access(session, project_id, tenant_id).neighborhood(
        node_id, sibling_limit=sibling_limit, child_limit=child_limit,
        sibling_offset=sibling_offset, child_offset=child_offset)


@router.get("/nodes/{node_id}/relations")
async def node_relations(project_id: str, node_id: str, session: SessionDep,
                         tenant_id: str = "local", relation_type: str | None = None,
                         offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).node_relations(
        node_id, relation_type=relation_type, offset=offset, limit=limit)


@router.get("/relations/between")
async def relation_between(project_id: str, left_node_id: str, right_node_id: str,
                           session: SessionDep, tenant_id: str = "local"):
    return await access(session, project_id, tenant_id).relation_between(
        left_node_id, right_node_id)


@router.get("/nodes/{node_id}/table-cells")
async def table_cells(project_id: str, node_id: str, session: SessionDep,
                      tenant_id: str = "local", offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).table_cells(
        node_id, offset=offset, limit=limit)


@router.get("/nodes/{node_id}/bm25-terms")
async def bm25_terms(project_id: str, node_id: str, session: SessionDep,
                     tenant_id: str = "local", run_id: str | None = None,
                     offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).bm25_terms(
        node_id, run_id=run_id, offset=offset, limit=limit)


@router.get("/nodes/{node_id}/similar-bm25")
async def similar_nodes_bm25(project_id: str, node_id: str, session: SessionDep,
                             tenant_id: str = "local", artifact_id: str | None = None,
                             offset: int = 0, limit: int = 20):
    return await access(session, project_id, tenant_id).similar_nodes_bm25(
        node_id, artifact_id=artifact_id, offset=offset, limit=limit)


@router.get("/tlr-runs")
async def run_history(project_id: str, session: SessionDep, tenant_id: str = "local",
                      offset: int = 0, limit: int = 30):
    return await access(session, project_id, tenant_id).run_history(offset=offset, limit=limit)


@router.get("/tlr-runs/{run_id}/annotations")
async def annotations(project_id: str, run_id: str, session: SessionDep,
                      tenant_id: str = "local", node_id: str | None = None,
                      section_id: str | None = None, role: str | None = None,
                      failures_only: bool = False, offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).annotations(
        run_id, node_id=node_id, section_id=section_id, role=role,
        failures_only=failures_only, offset=offset, limit=limit)


@router.get("/tlr-runs/{run_id}/nodes/{node_id}/results")
async def tlr_results(project_id: str, run_id: str, node_id: str,
                      session: SessionDep, tenant_id: str = "local",
                      offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).tlr_results(
        run_id, node_id, offset=offset, limit=limit)


@router.get("/tlr-runs/{run_id}/nodes/{node_id}/links")
async def trace_links(project_id: str, run_id: str, node_id: str,
                      session: SessionDep, tenant_id: str = "local",
                      offset: int = 0, limit: int = 50):
    return await access(session, project_id, tenant_id).trace_links(
        run_id, node_id, offset=offset, limit=limit)


@router.get("/tlr-runs/{run_id}/nodes/{node_id}/mandatory")
async def mandatory_tlr_nodes(project_id: str, run_id: str, node_id: str,
                              session: SessionDep, tenant_id: str = "local",
                              offset: int = 0, limit: int = 50,
                              review_id: str | None = None):
    target_artifact_ids = None
    target_node_ids = None
    if review_id:
        review = await session.get(Review, review_id)
        if review is None or review.project_id != project_id or review.tlr_run_id != run_id:
            raise ValueError("review scope is not available for this TLR run")
        target_artifact_ids = (review.scope_json or {}).get("target_artifact_ids")
        target_node_ids = (review.scope_json or {}).get("target_node_ids")
    return await access(session, project_id, tenant_id).mandatory_tlr_nodes(
        run_id, node_id, offset=offset, limit=limit,
        target_artifact_ids=target_artifact_ids, target_node_ids=target_node_ids)


@router.get("/tlr-runs/{run_id}")
async def run_detail(project_id: str, run_id: str, session: SessionDep,
                     tenant_id: str = "local", failure_offset: int = 0,
                     failure_limit: int = 50):
    return await access(session, project_id, tenant_id).run_detail(
        run_id, failure_offset=failure_offset, failure_limit=failure_limit)


@router.get("/tlr-runs/{run_id}/visualization")
async def visualization(project_id: str, run_id: str, session: SessionDep,
                        tenant_id: str = "local"):
    return await tlr_visualization(access(session, project_id, tenant_id), run_id)


@router.get("/tlr-runs/{run_id}/scope")
async def scope(project_id: str, run_id: str, session: SessionDep,
                tenant_id: str = "local"):
    """Return only TLR-participating nodes for analysis scope selection."""
    return await tlr_scope(access(session, project_id, tenant_id), run_id)


class DenseQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    offset: int = 0
    limit: int = 20


@router.post("/tlr-runs/{run_id}/search-dense")
async def search_dense(project_id: str, run_id: str, request: DenseQuery,
                       session: SessionDep, tenant_id: str = "local",
                       embedding: EmbeddingProvider = Depends(get_embedding_provider)):
    reader = access(session, project_id, tenant_id)
    await reader.run(run_id)
    instruction = "将此软件工程语段表示为用于检索相关设计证据的语义向量。"
    report = await embedding.query_input_length_report([request.query], instruction)
    if report.get("oversize_indexes"):
        raise ValueError("dense query exceeds embedding input limit")
    vector = (await embedding.encode_queries([request.query], instruction))[0]
    return await reader.dense_search(
        run_id, vector, offset=request.offset, limit=request.limit)
