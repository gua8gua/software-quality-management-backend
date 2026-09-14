import copy

from sqlalchemy import select

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.artifact_context import is_document
from app.modules.consistency.structure import artifact_fingerprint
from app.modules.model_config.models import ModelConnection, ModelTaskBinding
from app.modules.model_config.vault import CredentialVault
from app.modules.requirements.models import RequirementAnalysis
from app.modules.requirements.processing import analyze_artifact, cluster
from app.modules.tlr.models import TlrHierarchyNode
from app.modules.tlr.repository import TlrRepository
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


async def requirements_model(session, settings, tenant_id, fallback):
    for task in ("requirements_analysis", "architecture_extraction", "tlr_classification"):
        binding = await session.scalar(
            select(ModelTaskBinding).where(
                ModelTaskBinding.tenant_id == tenant_id, ModelTaskBinding.task == task
            )
        )
        if not binding:
            continue
        connection = await session.get(ModelConnection, binding.connection_id)
        if connection and connection.tenant_id == tenant_id:
            key = CredentialVault(settings).decrypt(connection.api_key_ciphertext)
            return OpenAICompatibleLLMProvider(
                base_url=connection.base_url,
                api_key=key or ("local" if connection.is_local else ""),
                model=binding.model_id,
                timeout_seconds=settings.llm_timeout_seconds,
                trust_env=not connection.is_local,
            ), {"task": task, "model_id": binding.model_id, "connection_id": connection.id}
    return fallback, {"task": ".env", "model_id": fallback.model}


async def load_analysis(session, identifier, tenant_id, project_id, dataset_id, artifacts):
    row = await TlrRepository(session).scoped(
        RequirementAnalysis, identifier, tenant_id, project_id
    )
    if row.dataset_id != dataset_id or row.status != "completed":
        raise AppError(ErrorCode.DATA_INVALID, "需求层级必须来自同一数据快照且已完成")
    by_id = {a.external_id: a for a in artifacts}
    for key, fingerprint in row.result["fingerprints"].items():
        if key not in by_id or artifact_fingerprint(by_id[key]) != fingerprint:
            raise AppError(ErrorCode.DATA_INVALID, "需求层级原文或元数据已变化，请重建")
    return row.result


class RequirementService:
    def __init__(self, session, settings, llm):
        self.session, self.settings, self.llm = session, settings, llm

    async def build(self, request):
        repo = TlrRepository(self.session)
        await repo.dataset(request.dataset_id, request.tenant_id, request.project_id)
        artifacts = await repo.artifacts(request.dataset_id)
        by_id = {a.external_id: a for a in artifacts}
        if len(set(request.requirement_ids)) != len(request.requirement_ids):
            raise AppError(ErrorCode.DATA_INVALID, "需求 ID 重复")
        if any(key not in by_id or not is_document(by_id[key]) for key in request.requirement_ids):
            raise AppError(ErrorCode.DATA_INVALID, "请选择有正文的生命周期文档制品")
        if any(
            (by_id[k].structure or {}).get("content_status") == "reference_only"
            for k in request.requirement_ids
        ):
            raise AppError(ErrorCode.DATA_INVALID, "需求缺少原文正文")
        row = RequirementAnalysis(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            status="running",
            request=request.model_dump(),
            result={},
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        try:
            llm, model_info = await requirements_model(
                self.session, self.settings, request.tenant_id, self.llm
            )
            result = {
                "version": "requirements-hierarchy-v1",
                "model": model_info,
                "requirements": {},
                "attempts": [],
                "fingerprints": {},
                "categories": {},
            }
            hierarchy = list(
                await self.session.scalars(
                    select(TlrHierarchyNode).where(
                        TlrHierarchyNode.dataset_id == request.dataset_id
                    )
                )
            )
            result["original_hierarchy"] = [
                {
                    "id": n.id,
                    "parent_id": n.parent_id,
                    "artifact_id": n.artifact_id,
                    "title": n.title,
                    "node_type": n.node_type,
                    "metadata": n.metadata_json,
                }
                for n in hierarchy
            ]
            for key in request.requirement_ids:
                result["requirements"][key] = await analyze_artifact(
                    by_id[key], request, llm, result["attempts"]
                )
                result["fingerprints"][key] = artifact_fingerprint(by_id[key])
                row.result = copy.deepcopy(result)
                await self.session.commit()
            summaries = [s for item in result["requirements"].values() for s in item["sections"]]
            result["categories"] = await cluster(
                summaries, llm, request.cluster_chars, result["attempts"]
            )
            for item in result["requirements"].values():
                for node in item["nodes"]:
                    memberships = []
                    section_ids = {
                        s["id"]
                        for s in item["sections"]
                        if s["start"] <= node["start"] and node["end"] <= s["end"]
                    }
                    for cat in result["categories"]["categories"]:
                        for sub in cat["subcategories"]:
                            if section_ids.intersection(sub["section_ids"]):
                                memberships.append(
                                    {
                                        "category": cat["key"],
                                        "subcategory": sub["key"],
                                        "title": cat["title"] + " / " + sub["title"],
                                        "summary": sub["summary"],
                                    }
                                )
                    node["categories"] = memberships
            row.result = copy.deepcopy(result)
            row.status = "completed"
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            row.status = "failed"
            await self.session.commit()
            raise
        return self.view(row)

    @staticmethod
    def view(row):
        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "project_id": row.project_id,
            "dataset_id": row.dataset_id,
            "status": row.status,
            "request": row.request,
            "result": row.result,
            "created_at": row.created_at.isoformat(),
        }
