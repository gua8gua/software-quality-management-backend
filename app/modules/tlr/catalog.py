"""Project-oriented read and upload API for the quality console; no arbitrary SQL access."""

import asyncio
import hashlib
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import SessionDep, SettingsDep
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.tlr.models import (
    TlrArtifact,
    TlrCandidate,
    TlrDataset,
    TlrElement,
    TlrFile,
    TlrHierarchyNode,
    TlrLink,
    TlrProject,
    TlrRun,
)
from app.modules.tlr.repository import TlrRepository
from app.modules.tlr.router import Scope, Service
from app.modules.tlr.schemas import (
    ArtifactInput,
    ArtifactView,
    CandidateView,
    Contract,
    DatasetInput,
    DatasetView,
    HierarchyNodeInput,
    HierarchyNodeView,
    Identifier,
    LinkView,
    Page,
    RunView,
    VisualizationProjectionInput,
)
from app.modules.tlr.uploads import KINDS, MAX_BYTES, TEXT_EXTENSIONS, extract_text
from app.modules.tlr.visualization import project_hierarchy
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/tlr", tags=["project-console"])


class ProjectInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    name: str = Field(min_length=1, max_length=255, pattern=r"\S")
    description: str = Field(default="", max_length=5000)


class UploadItem(Contract):
    external_id: Identifier
    kind: Identifier
    revision: Identifier

    @field_validator("kind")
    @classmethod
    def supported_kind(cls, value):
        if value not in KINDS:
            raise ValueError("请明确选择支持的生命周期文件、代码或测试用例类型")
        return value


async def project_exists(session, project_id, tenant_id):
    project = await session.get(TlrProject, (tenant_id, project_id))
    if not project:
        raise AppError(ErrorCode.NOT_FOUND, "项目不存在")
    return project


@router.get("/capabilities", response_model=ApiResponse[dict])
async def capabilities(settings: SettingsDep):
    return ApiResponse(
        data={
            "kinds": [{"id": key, "label": label} for key, label in KINDS.items()],
            "extensions": sorted(TEXT_EXTENSIONS | {".pdf", ".docx"}),
            "max_file_bytes": MAX_BYTES,
            "max_files": 50,
            "embedding_configured": bool(
                settings.model_api_key and not settings.model_api_key.startswith("your-")
            ),
            "llm_configured": bool(
                settings.llm_api_key and not settings.llm_api_key.startswith("your-")
            ),
        }
    )


@router.post("/projects", response_model=ApiResponse[dict])
async def create_project(request: ProjectInput, session: SessionDep):
    row = TlrProject(
        tenant_id=request.tenant_id,
        id=request.project_id,
        name=request.name,
        description=request.description,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(ErrorCode.DATA_DUPLICATE, "该项目标识已存在") from exc
    return ApiResponse(
        data={
            "id": row.id,
            "tenant_id": row.tenant_id,
            "name": row.name,
            "description": row.description,
        }
    )


@router.get("/projects", response_model=ApiResponse[Page])
async def projects(
    tenant_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    dataset_count = (
        select(func.count())
        .select_from(TlrDataset)
        .where(TlrDataset.tenant_id == TlrProject.tenant_id, TlrDataset.project_id == TlrProject.id)
        .correlate(TlrProject)
        .scalar_subquery()
    )
    run_count = (
        select(func.count())
        .select_from(TlrRun)
        .where(TlrRun.tenant_id == TlrProject.tenant_id, TlrRun.project_id == TlrProject.id)
        .correlate(TlrProject)
        .scalar_subquery()
    )
    records = await session.execute(
        select(TlrProject, dataset_count, run_count)
        .where(TlrProject.tenant_id == tenant_id)
        .order_by(TlrProject.created_at.desc(), TlrProject.id)
        .offset(offset)
        .limit(limit)
    )
    total = await session.scalar(
        select(func.count()).select_from(TlrProject).where(TlrProject.tenant_id == tenant_id)
    )
    return ApiResponse(
        data={
            "items": [
                {
                    "id": p.id,
                    "tenant_id": p.tenant_id,
                    "name": p.name,
                    "description": p.description,
                    "created_at": p.created_at.isoformat(),
                    "dataset_count": ds,
                    "run_count": rs,
                }
                for p, ds, rs in records
            ],
            "offset": offset,
            "limit": limit,
            "total": total,
        }
    )


@router.get("/projects/{project_id}", response_model=ApiResponse[dict])
async def project_detail(project_id: str, tenant_id: Scope, session: SessionDep):
    project = await project_exists(session, project_id, tenant_id)
    return ApiResponse(
        data={
            "id": project.id,
            "tenant_id": project.tenant_id,
            "name": project.name,
            "description": project.description,
        }
    )


@router.get("/projects/{project_id}/datasets", response_model=ApiResponse[Page])
async def project_datasets(
    project_id: str,
    tenant_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    await project_exists(session, project_id, tenant_id)
    return ApiResponse(
        data=await scoped_page(
            session, TlrDataset, DatasetView, tenant_id, project_id, offset, limit
        )
    )


@router.get("/projects/{project_id}/runs", response_model=ApiResponse[Page])
async def project_runs(
    project_id: str,
    tenant_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    await project_exists(session, project_id, tenant_id)
    return ApiResponse(
        data=await scoped_page(session, TlrRun, RunView, tenant_id, project_id, offset, limit)
    )


async def scoped_page(session, model, schema, tenant, project, offset, limit):
    conditions = [model.tenant_id == tenant, model.project_id == project]
    total = await session.scalar(select(func.count()).select_from(model).where(*conditions))
    rows = await session.scalars(
        select(model)
        .where(*conditions)
        .order_by(model.created_at.desc(), model.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [schema.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/datasets/{dataset_id}/inventory", response_model=ApiResponse[Page])
async def inventory(
    dataset_id: str,
    tenant_id: Scope,
    project_id: Scope,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    await TlrRepository(session).dataset(dataset_id, tenant_id, project_id)
    columns = [
        TlrArtifact.id,
        TlrArtifact.external_id,
        TlrArtifact.kind,
        TlrArtifact.revision,
        TlrArtifact.locator,
        TlrArtifact.sha256,
        TlrArtifact.original_file_id,
        TlrArtifact.structure,
        func.length(TlrArtifact.content).label("characters"),
    ]
    rows = await session.execute(
        select(*columns)
        .where(TlrArtifact.dataset_id == dataset_id)
        .order_by(TlrArtifact.external_id)
        .offset(offset)
        .limit(limit)
    )
    total = await session.scalar(
        select(func.count()).select_from(TlrArtifact).where(TlrArtifact.dataset_id == dataset_id)
    )
    return ApiResponse(
        data={
            "items": [dict(r) for r in rows.mappings()],
            "offset": offset,
            "limit": limit,
            "total": total,
        }
    )


@router.get("/datasets/{dataset_id}/hierarchy", response_model=ApiResponse[list[HierarchyNodeView]])
async def hierarchy(dataset_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep):
    await TlrRepository(session).dataset(dataset_id, tenant_id, project_id)
    rows = await session.scalars(
        select(TlrHierarchyNode)
        .where(TlrHierarchyNode.dataset_id == dataset_id)
        .order_by(TlrHierarchyNode.ordinal, TlrHierarchyNode.node_key)
    )
    return ApiResponse(data=[HierarchyNodeView.model_validate(row) for row in rows])


async def scoped_artifact(session, artifact_id, tenant_id, project_id):
    row = await session.scalar(
        select(TlrArtifact)
        .join(TlrDataset)
        .where(
            TlrArtifact.id == artifact_id,
            TlrDataset.tenant_id == tenant_id,
            TlrDataset.project_id == project_id,
        )
    )
    if not row:
        raise AppError(ErrorCode.NOT_FOUND, "制品不存在")
    return row


@router.get("/artifacts/{artifact_id}", response_model=ApiResponse[ArtifactView])
async def artifact_detail(
    artifact_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep
):
    row = await scoped_artifact(session, artifact_id, tenant_id, project_id)
    return ApiResponse(data=ArtifactView.model_validate(row))


@router.get("/artifacts/{artifact_id}/download", response_class=Response)
async def download(artifact_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep):
    row = await scoped_artifact(session, artifact_id, tenant_id, project_id)
    original = await session.get(TlrFile, row.original_file_id) if row.original_file_id else None
    payload = original.payload if original else row.content.encode("utf-8")
    filename = original.filename if original else f"{row.external_id}.txt"
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/projects/{project_id}/upload", response_model=ApiResponse[DatasetView])
async def upload(
    project_id: str,
    service: Service,
    session: SessionDep,
    tenant_id: Annotated[str, Form(min_length=1, max_length=128)],
    version: Annotated[str, Form(min_length=1, max_length=128)],
    metadata: Annotated[str, Form(max_length=100_000)],
    files: Annotated[list[UploadFile], File()],
    base_dataset_id: Annotated[str | None, Form()] = None,
    replace_existing: Annotated[bool, Form()] = False,
):
    await project_exists(session, project_id, tenant_id)
    try:
        items = TypeAdapter(list[UploadItem]).validate_json(metadata)
        if not 1 <= len(files) <= 50 or len(items) != len(files):
            raise ValueError("每个文件都必须有对应的类型、标识和版本，单批最多 50 个")
        if len({i.external_id for i in items}) != len(items):
            raise ValueError("本批次文件标识重复")
        previous, original_ids, previous_hierarchy = {}, {}, []
        if base_dataset_id:
            await service.repo.dataset(base_dataset_id, tenant_id, project_id)
            base_artifacts = await service.repo.artifacts(base_dataset_id)
            base_external_by_id = {artifact.id: artifact.external_id for artifact in base_artifacts}
            for artifact in base_artifacts:
                previous[artifact.external_id] = ArtifactInput(
                    external_id=artifact.external_id,
                    kind=artifact.kind,
                    revision=artifact.revision,
                    content=artifact.content,
                    locator=artifact.locator,
                    structure=artifact.structure,
                )
                original_ids[artifact.external_id] = artifact.original_file_id
            base_nodes = list(
                await session.scalars(
                    select(TlrHierarchyNode).where(TlrHierarchyNode.dataset_id == base_dataset_id)
                )
            )
            key_by_id = {node.id: node.node_key for node in base_nodes}
            previous_hierarchy = [
                HierarchyNodeInput(
                    node_key=node.node_key,
                    parent_key=key_by_id.get(node.parent_id),
                    artifact_external_id=base_external_by_id.get(node.artifact_id),
                    title=node.title,
                    node_type=node.node_type,
                    ordinal=node.ordinal,
                    metadata=node.metadata_json,
                )
                for node in base_nodes
            ]
        size = 0
        uploaded = []
        for file, item in zip(files, items, strict=True):
            if item.external_id in previous and not replace_existing:
                raise ValueError("同标识制品已存在；需选择替换或修改标识，旧快照仍将保留")
            name = (file.filename or "").replace("\\", "/").split("/")[-1]
            if not name or len(name) > 255:
                raise ValueError("文件名为空或过长")
            data = await file.read(MAX_BYTES + 1)
            size += len(data)
            if size > 50 * 1024 * 1024:
                raise ValueError("本批次总文件大小超过 50 MiB")
            try:
                text = await asyncio.to_thread(extract_text, name, data)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"无法解析文件 {name}，请检查文件格式或导出为 UTF-8 文本") from exc
            previous[item.external_id] = ArtifactInput(
                **item.model_dump(), content=text, locator=name
            )
            uploaded.append(
                (
                    item.external_id,
                    TlrFile(
                        filename=name,
                        media_type=(file.content_type or "application/octet-stream")[:128],
                        payload=data,
                        sha256=hashlib.sha256(data).hexdigest(),
                    ),
                )
            )
            if previous_hierarchy and not any(
                node.artifact_external_id == item.external_id for node in previous_hierarchy
            ):
                previous_hierarchy.append(
                    HierarchyNodeInput(
                        node_key=f"artifact:{item.external_id}",
                        artifact_external_id=item.external_id,
                        title=item.external_id,
                        node_type="artifact",
                        ordinal=len(previous_hierarchy),
                        metadata={"source": "local-upload"},
                    )
                )
        request = DatasetInput(
            tenant_id=tenant_id,
            project_id=project_id,
            version=version,
            provenance={
                "source": "local-upload",
                "base_dataset_id": base_dataset_id,
                "project_assignment": "user",
                "kind_assignment": "user",
            },
            artifacts=list(previous.values()),
            hierarchy=previous_hierarchy,
        )
        dataset = await service.import_dataset(request, commit=False)
        for external_id, original in uploaded:
            session.add(original)
            await session.flush()
            original_ids[external_id] = original.id
        for artifact in await service.repo.artifacts(dataset.id):
            artifact.original_file_id = original_ids.get(artifact.external_id)
        await session.commit()
        return ApiResponse(data=DatasetView.model_validate(dataset))
    except (ValueError, ValidationError) as exc:
        await session.rollback()
        message = (
            str(exc)
            if not isinstance(exc, ValidationError)
            else "文件元信息不合法：需填写标识、类型和版本"
        )
        raise AppError(ErrorCode.DATA_INVALID, message[:500]) from exc
    finally:
        for file in files:
            await file.close()


@router.get("/runs/{run_id}/visualization", response_model=ApiResponse[dict])
async def visualization(run_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep):
    return ApiResponse(
        data=await _visualization_data(run_id, tenant_id, project_id, session, [], [])
    )


@router.post("/runs/{run_id}/visualization/projection", response_model=ApiResponse[dict])
async def visualization_projection(
    run_id: str,
    request: VisualizationProjectionInput,
    tenant_id: Scope,
    project_id: Scope,
    session: SessionDep,
):
    return ApiResponse(
        data=await _visualization_data(
            run_id,
            tenant_id,
            project_id,
            session,
            request.collapsed_source,
            request.collapsed_target,
        )
    )


async def _visualization_data(
    run_id, tenant_id, project_id, session, collapsed_source, collapsed_target
):
    repo = TlrRepository(session)
    run = await repo.run(run_id, tenant_id, project_id)
    elements = await session.execute(
        select(
            TlrElement.id, TlrElement.artifact_id, TlrElement.external_id, TlrElement.role
        ).where(TlrElement.run_id == run_id)
    )
    candidates = await session.execute(
        select(
            TlrCandidate.id,
            TlrCandidate.source_element_id,
            TlrCandidate.target_element_id,
            TlrCandidate.rank,
            TlrCandidate.similarity,
            TlrCandidate.decision,
        ).where(TlrCandidate.run_id == run_id)
    )
    links = await repo.rows(TlrLink, run_id)
    artifacts = list(
        await session.scalars(select(TlrArtifact).where(TlrArtifact.dataset_id == run.dataset_id))
    )
    by_external_id = {artifact.external_id: artifact.id for artifact in artifacts}
    hierarchy_nodes = list(
        await session.scalars(
            select(TlrHierarchyNode).where(TlrHierarchyNode.dataset_id == run.dataset_id)
        )
    )
    projection = project_hierarchy(
        hierarchy_nodes,
        links,
        [by_external_id[value] for value in run.config["source_ids"] if value in by_external_id],
        [by_external_id[value] for value in run.config["target_ids"] if value in by_external_id],
        {"source": set(collapsed_source), "target": set(collapsed_target)},
    )
    return {
        "elements": [dict(r) for r in elements.mappings()],
        "candidates": [dict(r) for r in candidates.mappings()],
        "links": [LinkView.model_validate(r).model_dump() for r in links],
        "hierarchy": [HierarchyNodeView.model_validate(r).model_dump() for r in hierarchy_nodes],
        "projection": projection,
        "failures": run.manifest.get("node_failures", []),
    }


@router.get("/runs/{run_id}/candidates/{candidate_id}", response_model=ApiResponse[dict])
async def candidate_detail(
    run_id: str, candidate_id: str, tenant_id: Scope, project_id: Scope, session: SessionDep
):
    await TlrRepository(session).run(run_id, tenant_id, project_id)
    row = await session.scalar(
        select(TlrCandidate).where(TlrCandidate.id == candidate_id, TlrCandidate.run_id == run_id)
    )
    if not row:
        raise AppError(ErrorCode.NOT_FOUND, "候选不存在")
    details = {"candidate": CandidateView.model_validate(row).model_dump()}
    for role, identifier in [("source", row.source_element_id), ("target", row.target_element_id)]:
        e = await session.get(TlrElement, identifier)
        details[role] = {
            "artifact_id": e.artifact_id,
            "external_id": e.external_id,
            "kind": e.kind,
            "start": e.start,
            "end": e.end,
            "content": e.content,
            "processing": e.processing,
        }
    return ApiResponse(data=details)
