import copy
import json
from collections import Counter

from sqlalchemy import select, update

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.artifact_context import ArtifactContext, has_body, is_document
from app.modules.consistency.compatibility import project_result
from app.modules.consistency.judge import (
    PROMPT_VERSION,
    conclude,
    error_record,
    judge,
    labels_for,
    parse_judgement,
    primary_target,
)
from app.modules.consistency.models import ConsistencyReview, ConsistencyRun
from app.modules.consistency.schemas import AnalysisInput
from app.modules.consistency.structure import (
    artifact_fingerprint,
    collect,
    digest,
    document_sections,
    evidence,
)
from app.modules.model_config.models import ModelConnection, ModelTaskBinding
from app.modules.model_config.vault import CredentialVault
from app.modules.tlr.models import TlrCandidate, TlrElement, TlrHierarchyNode
from app.modules.tlr.repository import TlrRepository
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


class ConsistencyService:
    def __init__(self, session, settings, llm):
        self.session, self.settings, self.llm = session, settings, llm
        self.repo = TlrRepository(session)

    async def get(self, run_id, tenant_id, project_id):
        return await self.repo.scoped(ConsistencyRun, run_id, tenant_id, project_id)

    async def create(self, request: AnalysisInput):
        tlr = await self.repo.run(request.tlr_run_id, request.tenant_id, request.project_id)
        if tlr.status != "completed":
            raise AppError(ErrorCode.DATA_INVALID, "请先完成 TLR 运行")
        artifacts = await self.repo.artifacts(tlr.dataset_id)
        by_external = {a.external_id: a for a in artifacts}
        by_id = {a.id: a for a in artifacts}
        sources = tlr.config["source_ids"]
        analysis = tlr.manifest.get("requirement_hierarchy")
        if request.requirements_run_id:
            from app.modules.requirements.service import load_analysis

            analysis = await load_analysis(
                self.session,
                request.requirements_run_id,
                request.tenant_id,
                request.project_id,
                tlr.dataset_id,
                artifacts,
            )
        eligible = {a.external_id for a in artifacts if is_document(a) and has_body(a)}
        requested = request.requirement_ids or [i for i in sources if i in eligible]
        if not requested or len(requested) > 5000:
            raise AppError(ErrorCode.DATA_INVALID, "每次请选择 1–5000 份源文档")
        if any(i not in sources or i not in eligible for i in requested):
            raise AppError(
                ErrorCode.DATA_INVALID,
                "源文档必须属于本次 TLR 源端且有正文",
            )
        target_ids = request.target_ids or [
            i
            for i in tlr.config["target_ids"]
            if i in by_external
            and (
                is_document(by_external[i])
                if request.analysis_kind == "document"
                else by_external[i].kind in {"code", "test_code"}
            )
        ]
        if not target_ids or any(
            i not in tlr.config["target_ids"]
            or i in requested
            or i not in by_external
            or not has_body(by_external[i])
            or (
                not is_document(by_external[i])
                if request.analysis_kind == "document"
                else by_external[i].kind not in {"code", "test_code"}
            )
            for i in target_ids
        ):
            raise AppError(
                ErrorCode.DATA_INVALID, "目标必须是 TLR 目标端有正文且符合分析类型的工件"
            )
        context = await ArtifactContext.prepare(
            self.session,
            artifacts,
            request.tenant_id,
            request.project_id,
            tlr.dataset_id,
            self.settings.artifact_context_cache_dir,
        )
        if any(i not in by_external for i in request.additional_artifact_ids):
            raise AppError(ErrorCode.DATA_INVALID, "补充工件必须属于同一快照")
        elements = {e.id: e for e in await self.repo.rows(TlrElement, tlr.id)}
        candidates = await self.repo.rows(TlrCandidate, tlr.id)
        hierarchy = list(
            await self.session.scalars(
                select(TlrHierarchyNode).where(TlrHierarchyNode.dataset_id == tlr.dataset_id)
            )
        )
        hierarchy_data = [
            dict(
                id=n.id,
                parent_id=n.parent_id,
                artifact_id=by_id[n.artifact_id].external_id if n.artifact_id else None,
                title=n.title,
                node_type=n.node_type,
                provenance=n.metadata_json,
            )
            for n in hierarchy
        ]
        by_node = {n.id: n for n in hierarchy}
        packets, links = {}, []
        for key in requested:
            req = by_external[key]
            if (req.structure or {}).get("content_status") == "reference_only":
                raise AppError(ErrorCode.DATA_INVALID, f"需求 {key} 缺少原文正文")
            related = [
                c
                for c in candidates
                if c.decision == "related"
                and elements[c.source_element_id].artifact_id == req.id
                and elements[c.target_element_id].external_id in target_ids
            ]
            target_elements = {c.target_element_id: elements[c.target_element_id] for c in related}
            packet = collect(
                req,
                artifacts,
                list(target_elements.values()),
                [
                    by_external[i]
                    for i in dict.fromkeys(
                        (target_ids if request.analysis_kind == "document" else [])
                        + request.additional_artifact_ids
                    )
                ],
                request.max_evidence_chars,
                context=context,
            )
            packet.update(
                analysis_kind=request.analysis_kind,
                document_relation=request.document_relation,
                target_ids=target_ids,
                linked_target_ids=list(
                    dict.fromkeys(e.external_id for e in target_elements.values())
                ),
                source_context=context.around(key, 0, len(req.content)),
            )
            packet["target_scope_complete"] = all(
                any(
                    e["artifact_id"] == i
                    and e["start"] == 0
                    and e["end"] == len(by_external[i].content)
                    for e in packet["evidence"]
                )
                for i in target_ids
            )
            # Context is available for interpretation, never a substitute for target coverage.
            packet["omitted"].extend(packet["source_context"]["omitted"])
            for target_id in dict.fromkeys([key, *target_ids]):
                for reference_id in context.related_ids(target_id):
                    if reference_id in by_external and has_body(by_external[reference_id]):
                        a = by_external[reference_id]
                        if not any(e["artifact_id"] == reference_id for e in packet["evidence"]):
                            if (
                                sum(len(e["text"]) for e in packet["evidence"]) + len(a.content)
                                <= request.max_evidence_chars
                            ):
                                packet["evidence"].append(
                                    evidence(a, 0, len(a.content), "parent_or_reference")
                                )
                            else:
                                packet["omitted"].append(
                                    {
                                        "artifact_id": reference_id,
                                        "reason": "reference_context_budget",
                                    }
                                )
            if analysis and key in analysis["requirements"]:
                from app.modules.tlr.hierarchical import terminal_units

                packet["requirement_units"] = terminal_units(analysis, key)
                packet["semantic_categories"] = analysis["categories"]
                packet["requirements_run_id"] = request.requirements_run_id or tlr.manifest.get(
                    "requirements_run_id"
                )
                packet["tlr_unit_targets"] = {}
                for candidate in related:
                    source_unit = elements[candidate.source_element_id].processing.get("unit_id")
                    target_artifact = elements[candidate.target_element_id].external_id
                    packet["tlr_unit_targets"].setdefault(source_unit, []).append(target_artifact)
            packet["matching_scope_incomplete"] = (
                tlr.stage == "completed_with_errors"
                or bool(tlr.manifest.get("partial"))
                or any(c.decision == "pending" for c in candidates)
                or not tlr.manifest.get("hierarchical_retrieval", {}).get("scope_complete", True)
            )
            if request.analysis_kind == "document":
                packet["matching_scope_incomplete"] = not packet["target_scope_complete"]
            ancestry, seen = [], set()
            node = next((n for n in hierarchy if n.artifact_id == req.id), None)
            context_used = len(req.content)
            while node is not None and node.id not in seen:
                seen.add(node.id)
                entry = {"title": node.title, "node_type": node.node_type}
                if node.artifact_id and node.artifact_id != req.id:
                    parent = by_id[node.artifact_id]
                    if context_used + len(parent.content) <= request.max_evidence_chars:
                        entry.update(text=parent.content, artifact_id=parent.external_id)
                        context_used += len(parent.content)
                    else:
                        packet["omitted"].append(
                            {
                                "artifact_id": parent.external_id,
                                "reason": "requirement_context_budget",
                            }
                        )
                ancestry.append(entry)
                node = by_node.get(node.parent_id)
            packet["requirement"]["ancestry"] = ancestry
            packet["requirement"]["document_sections"] = document_sections(req)
            if len(req.content) > request.max_evidence_chars and not packet.get(
                "requirement_units"
            ):
                raise AppError(
                    ErrorCode.DATA_INVALID,
                    f"需求 {key} 超过输入预算，请选择文档原有章节作为需求制品",
                )
            packets[key] = packet
            for c in related:
                target = elements[c.target_element_id]
                links.append(
                    dict(
                        requirement_id=key,
                        artifact_id=by_id[target.artifact_id].external_id,
                        element_id=target.id,
                        candidate_id=c.id,
                        relation="tlr_related",
                        source_unit_id=elements[c.source_element_id].processing.get("unit_id"),
                        source_element_id=c.source_element_id,
                        target_start=target.start,
                        target_end=target.end,
                        similarity=c.similarity,
                        evidence=c.evidence,
                    )
                )
        structures = {
            a.external_id: context.data[a.external_id]["code"]
            for a in artifacts
            if a.kind in {"code", "test_code"}
        }
        snapshot = {
            "dataset_id": tlr.dataset_id,
            "tlr_run_id": tlr.id,
            "tlr_stage": tlr.stage,
            "tlr_partial": tlr.stage == "completed_with_errors" or bool(tlr.manifest.get("partial")),
            "analysis_kind": request.analysis_kind,
            "document_relation": request.document_relation,
            "target_ids": target_ids,
            "artifact_context": context.manifest(),
            "prompt_version": PROMPT_VERSION,
            "packets": packets,
            "requirement_structure": {
                key: analysis["requirements"][key]
                for key in requested
                if analysis and key in analysis["requirements"]
            },
            "tlr_elements": [
                {
                    "id": e.id,
                    "artifact_id": e.external_id,
                    "role": e.role,
                    "start": e.start,
                    "end": e.end,
                    "content": e.content,
                    "processing": e.processing,
                }
                for e in elements.values()
                if (e.role == "source" and e.external_id in requested)
                or (e.role == "target" and e.external_id in target_ids)
            ],
            "artifact_hashes": {a.external_id: digest(a.content) for a in artifacts},
            "artifact_fingerprints": {a.external_id: artifact_fingerprint(a) for a in artifacts},
            "hierarchy": hierarchy_data,
            "code_structure": structures,
            "document_structure": {i: entry["sections"] for i, entry in context.data.items()},
            "links": links,
            "tlr_pending_candidates": sum(c.decision == "pending" for c in candidates),
            "scope": "imported_snapshot_only",
        }
        snapshot["digest"] = digest(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        run = ConsistencyRun(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            tlr_run_id=tlr.id,
            status="prepared",
            request=request.model_dump(),
            snapshot=snapshot,
            results={},
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def model(self, tenant_id):
        for task in ("consistency_judgement", "tlr_classification"):
            binding = await self.session.scalar(
                select(ModelTaskBinding).where(
                    ModelTaskBinding.tenant_id == tenant_id, ModelTaskBinding.task == task
                )
            )
            if binding:
                connection = await self.session.get(ModelConnection, binding.connection_id)
                if connection and connection.tenant_id == tenant_id:
                    key = CredentialVault(self.settings).decrypt(connection.api_key_ciphertext)
                    return OpenAICompatibleLLMProvider(
                        base_url=connection.base_url,
                        api_key=key or "local",
                        model=binding.model_id,
                        timeout_seconds=self.settings.llm_timeout_seconds,
                        trust_env=not connection.is_local,
                    ), {
                        "source": task,
                        "model_id": binding.model_id,
                        "connection_id": connection.id,
                    }
        return self.llm, {"source": ".env", "model_id": self.llm.model}

    async def execute(self, run_id, tenant_id, project_id):
        run = await self.get(run_id, tenant_id, project_id)
        claimed = await self.session.execute(
            update(ConsistencyRun)
            .where(ConsistencyRun.id == run_id, ConsistencyRun.status.in_(["prepared", "failed"]))
            .values(status="running")
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise AppError(
                ErrorCode.DATA_DUPLICATE, "该一致性运行已完成或正在执行；新判断请创建新运行"
            )
        await self.session.commit()
        await self.session.refresh(run)
        try:
            artifacts = await self.repo.artifacts(run.snapshot["dataset_id"])
            if {a.external_id: digest(a.content) for a in artifacts} != run.snapshot[
                "artifact_hashes"
            ]:
                raise AppError(ErrorCode.DATA_INVALID, "快照正文已变化，请创建新运行")
            by_external = {a.external_id: a for a in artifacts}
            if (
                "artifact_fingerprints" in run.snapshot
                and {a.external_id: artifact_fingerprint(a) for a in artifacts}
                != run.snapshot["artifact_fingerprints"]
            ):
                raise AppError(ErrorCode.DATA_INVALID, "快照元数据已变化，请创建新运行")
            llm, model_info = await self.model(tenant_id)
            options = AnalysisInput.model_validate(run.request)
            catalog = [
                {
                    "id": a.external_id,
                    "kind": a.kind,
                    "locator": a.locator,
                    "title": (a.structure or {}).get("title", ""),
                    "symbols": [
                        n["name"]
                        for n in run.snapshot["code_structure"]
                        .get(a.external_id, {})
                        .get("nodes", [])
                    ][:30],
                }
                for a in artifacts
                if a.kind != "package"
                and (a.structure or {}).get("content_status") != "reference_only"
            ]
            # Bound metadata as well as source text; never silently imply exhaustive search.
            catalog_incomplete = len(catalog) > 300
            catalog = catalog[:300]
            available = {entry["id"] for entry in catalog}
            results = copy.deepcopy(run.results)
            results.pop("error", None)
            results["model"] = model_info
            results["available_artifacts"] = catalog
            results["prompt_version"] = PROMPT_VERSION
            results.setdefault("requirements", {})
            for key, original in run.snapshot["packets"].items():
                if key in results["requirements"] and not results["requirements"][key].get("error"):
                    continue
                packet, attempts = copy.deepcopy(original), []
                packet_catalog = [entry for entry in catalog if entry["id"] != key]
                if catalog_incomplete:
                    packet["omitted"].append({"reason": "artifact_catalog_budget"})
                try:
                    for round_index in range(options.max_expansion_rounds + 1):
                        raw = await judge(llm, packet, packet_catalog)
                        attempts.append(
                            {
                                "round": round_index,
                                "response": raw,
                                "evidence_ids": [e["id"] for e in packet["evidence"]],
                            }
                        )
                        judgement = parse_judgement(raw, packet)
                        if (
                            not judgement.requested_artifact_ids
                            or round_index == options.max_expansion_rounds
                        ):
                            break
                        added = False
                        for artifact_id in dict.fromkeys(judgement.requested_artifact_ids):
                            if artifact_id not in available or artifact_id == key:
                                packet["omitted"].append(
                                    {"artifact_id": artifact_id, "reason": "not_in_catalog"}
                                )
                                continue
                            a = by_external[artifact_id]
                            entry = evidence(a, 0, len(a.content), "model_requested_context")
                            if any(e["id"] == entry["id"] for e in packet["evidence"]):
                                continue
                            if (
                                sum(len(e["text"]) for e in packet["evidence"]) + len(a.content)
                                > options.max_evidence_chars
                            ):
                                packet["omitted"].append(
                                    {"artifact_id": artifact_id, "reason": "expansion_budget"}
                                )
                                continue
                            packet["evidence"].append(entry)
                            added = True
                        if not added:
                            break
                    result = conclude(packet, judgement)
                    result.update(packet=packet, attempts=attempts)
                except Exception as exc:
                    result = {
                        "schema_version": 2,
                        "outcomes": ["judgement_error"],
                        "outcome_labels": ["判断出错"],
                        "analysis_kind": options.analysis_kind,
                        "implementations": [
                            error_record(packet["requirement"]["text"][:4000], type(exc).__name__)
                        ],
                        "whole_requirement_implemented": False,
                        "verification_status": "unverified",
                        "error": type(exc).__name__,
                        "packet": packet,
                        "attempts": attempts,
                    }
                results["requirements"][key] = result
                run.results = copy.deepcopy(results)
                await self.session.commit()
            run.status = (
                "failed"
                if any(r.get("error") for r in results["requirements"].values())
                else "completed"
            )
            await self.session.commit()
        except BaseException as exc:
            await self.session.rollback()
            # Persist a resumable failure without exposing provider messages/credentials.
            await self.session.execute(
                update(ConsistencyRun).where(ConsistencyRun.id == run_id).values(status="failed")
            )
            await self.session.commit()
            if isinstance(exc, AppError) or not isinstance(exc, Exception):
                raise
            raise AppError(
                ErrorCode.SERVER_ERROR,
                "一致性运行失败，可重新执行",
                data={"error_type": type(exc).__name__},
            ) from exc
        return await self.get(run_id, tenant_id, project_id)

    async def view(self, run):
        reviews = list(
            await self.session.scalars(
                select(ConsistencyReview)
                .where(ConsistencyReview.run_id == run.id)
                .order_by(ConsistencyReview.created_at, ConsistencyReview.id)
            )
        )
        results = copy.deepcopy(run.results)
        results["requirements"] = {
            k: project_result(r) for k, r in results.get("requirements", {}).items()
        }
        counts = dict(Counter(s for r in results["requirements"].values() for s in r["outcomes"]))
        mappings = []
        for requirement_id, result in results["requirements"].items():
            for index, record in enumerate(result.get("implementations", [])):
                for citation in record["citations"]:
                    if not primary_target(result.get("packet", {}), citation):
                        continue
                    structures = run.snapshot["code_structure"].get(citation["artifact_id"], {})
                    nodes = [
                        n
                        for n in structures.get("nodes", [])
                        if n["start"] <= citation["artifact_start"]
                        and citation["artifact_start"] + len(citation["quote"]) <= n["end"]
                    ]
                    symbol = min(nodes, key=lambda n: n["end"] - n["start"]) if nodes else None
                    mappings.append(
                        {
                            "requirement_id": requirement_id,
                            "source_id": requirement_id,
                            "artifact_id": citation["artifact_id"],
                            "symbol_id": symbol["id"] if symbol else None,
                            "implementation_index": index,
                            "parts": record["parts"],
                            "citation": citation,
                            "verification_status": "unverified",
                        }
                    )
        return dict(
            id=run.id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            status=run.status,
            analysis_kind=run.snapshot.get("analysis_kind", "code"),
            document_relation=run.snapshot.get("document_relation", "refinement"),
            created_at=run.created_at.isoformat(),
            request=run.request,
            snapshot=run.snapshot,
            results=results,
            implementation_mappings=mappings,
            outcome_dictionary=labels_for(run.snapshot),
            metrics={
                "selected_requirements": len(run.snapshot["packets"]),
                "processed_requirements": len(results["requirements"]),
                "outcome_counts": counts,
                "meaning": "Non-exclusive labels; sums may exceed requirement count.",
            },
            reviews=[
                dict(id=r.id, created_at=r.created_at.isoformat(), **r.payload) for r in reviews
            ],
        )

    async def review(self, run_id, request):
        run = await self.get(run_id, request.tenant_id, request.project_id)
        if request.snapshot_digest != run.snapshot["digest"]:
            raise AppError(ErrorCode.DATA_INVALID, "复核证据与分析快照不匹配")
        result = run.results.get("requirements", {}).get(request.requirement_id)
        if not result or result.get("error"):
            raise AppError(ErrorCode.DATA_INVALID, "需求尚无有效判断可供复核")
        row = ConsistencyReview(run_id=run.id, payload=request.model_dump())
        self.session.add(row)
        await self.session.commit()
        return await self.view(run)
