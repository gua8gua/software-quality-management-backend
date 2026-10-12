import asyncio
import hashlib
from collections import Counter, defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.execution import (
    ExecutionStatus,
    ResultValidity,
    ValidationStatus,
    classify_exception,
    fingerprint_payload,
    prompt_hash,
    schema_hash,
)
from app.modules.artifacts.file_types import detect_file_type
from app.modules.artifacts.models import (
    Artifact,
    ContentNode,
    ContentRelation,
    File,
    Folder,
    Project,
)
from app.modules.artifacts.schemas import ArtifactCreate
from app.modules.parsing.service import parse_file
from app.modules.segmentation.service import segment
from app.modules.traceability.evidence import assemble_evidence, structural_expand
from app.modules.traceability.models import (
    Candidate,
    RetrievalProjection,
    SemanticAnnotation,
    TlrRun,
    TraceLink,
    TraceUnit,
)
from app.modules.traceability.projections import (
    document_type,
    heading_path,
    node_content,
    raw_projection,
    synopsis_projection,
)
from app.modules.traceability.prompts import (
    DENSE_TASK_INSTRUCTION,
    FINE_TRACE_PROMPT_VERSION,
    FINE_TRACE_SYSTEM_PROMPT,
    SEMANTIC_ANNOTATION_PROMPT_VERSION,
)
from app.modules.traceability.retrievers import (
    BM25Retriever,
    ChineseTokenizer,
    DenseRetriever,
    HybridCandidate,
    union_candidates,
)
from app.modules.traceability.schemas import RunCreate
from app.modules.traceability.semantic import SemanticAnnotator
from app.modules.traceability.validation import FineTraceModelOutput, FineTraceValidator
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider


class TraceabilityService:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMProvider | None = None,
        embedding: EmbeddingProvider | None = None,
    ):
        self.session = session
        self.llm = llm
        self.embedding = embedding

    async def ensure_project(
        self, tenant_id: str, project_id: str, name: str | None = None
    ) -> Project:
        project = await self.session.get(Project, project_id)
        if project is None:
            project = Project(tenant_id=tenant_id, id=project_id, name=name or project_id)
            self.session.add(project)
            await self.session.flush()
        elif project.tenant_id != tenant_id:
            raise ValueError("project not found in tenant scope")
        return project

    async def ingest_file(
        self,
        tenant_id: str,
        project_id: str,
        filename: str,
        payload: bytes,
        request: ArtifactCreate,
    ) -> Artifact:
        await self.ensure_project(tenant_id, project_id)
        detected_format, detected_mime = detect_file_type(filename, payload)
        suffix = filename.rsplit(".", 1)[-1].lower()
        if suffix in {"pdf", "docx"} and detected_format != suffix:
            raise ValueError("file signature does not match PDF/DOCX filename")
        if request.folder_id:
            folder = await self.session.get(Folder, request.folder_id)
            if folder is None or folder.project_id != project_id:
                raise ValueError("folder not found in project scope")
        file = File(
            project_id=project_id,
            filename=filename,
            media_type=detected_mime,
            file_format=detected_format,
            sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload,
        )
        self.session.add(file)
        await self.session.flush()
        artifact = Artifact(
            project_id=project_id,
            file_id=file.id,
            folder_id=request.folder_id,
            external_id=request.external_id,
            revision=request.revision,
            engineering_role=request.engineering_role,
            modality=request.modality,
            file_format=detected_format,
            language=request.language,
        )
        self.session.add(artifact)
        await self.session.flush()
        await self._build_content(artifact, payload, filename)
        await self.session.commit()
        return artifact

    async def _build_content(self, artifact: Artifact, payload: bytes, filename: str) -> None:
        parsed = parse_file(filename, payload, artifact.modality, artifact.language)
        if parsed.modality != artifact.modality:
            raise ValueError("file format does not match selected text/code type")
        artifact.language = parsed.language
        artifact.file_format = parsed.file_format
        segmented = segment(parsed)
        node_rows = []
        for draft in segmented.nodes:
            node = ContentNode(
                artifact_id=artifact.id,
                sibling_ordinal=draft.sibling_ordinal,
                reading_order=draft.reading_order,
                node_type=draft.node_type,
                original_type=draft.original_type,
                title=draft.title,
                text=draft.text,
                docling_ref=draft.docling_ref,
                docling_parent_ref=draft.docling_parent_ref,
                docling_children_refs=draft.docling_children_refs,
                docling_depth=draft.docling_depth,
                heading_level=draft.heading_level,
                content_layer=draft.content_layer,
                hierarchy_status=draft.hierarchy_status,
                hierarchy_source=draft.hierarchy_source,
                provenance_json=draft.provenance,
                content_json=draft.content_data,
                metadata_json=draft.metadata,
            )
            node_rows.append(node)
            self.session.add(node)
            await self.session.flush()
        for index, draft in enumerate(segmented.nodes):
            if draft.parent_index is not None and draft.parent_index != index:
                node_rows[index].parent_id = node_rows[draft.parent_index].id
        await self.session.flush()
        for relation in segmented.relations:
            self.session.add(
                ContentRelation(
                    artifact_id=artifact.id,
                    source_node_id=node_rows[relation.source_index].id,
                    target_node_id=(
                        node_rows[relation.target_index].id
                        if relation.target_index is not None
                        else None
                    ),
                    relation_type=relation.relation_type,
                    status=relation.status,
                    origin=relation.origin,
                    source_text=relation.source_text,
                    target_docling_ref=relation.target_docling_ref,
                    metadata_json=relation.metadata,
                )
            )

    async def create_run(self, request: RunCreate) -> TlrRun:
        await self.ensure_project(request.tenant_id, request.project_id)
        source = list(
            await self.session.scalars(
                select(Artifact).where(
                    Artifact.project_id == request.project_id,
                    Artifact.id.in_(request.source_artifact_ids),
                )
            )
        )
        target = list(
            await self.session.scalars(
                select(Artifact).where(
                    Artifact.project_id == request.project_id,
                    Artifact.id.in_(request.target_artifact_ids),
                )
            )
        )
        if len(source) != len(request.source_artifact_ids) or len(target) != len(
            request.target_artifact_ids
        ):
            raise ValueError("source or target artifact does not belong to project")
        if any(item.engineering_role != "requirement" for item in source):
            raise ValueError("all source artifacts must have engineering_role='requirement'")
        if any(item.engineering_role != "design" for item in target):
            raise ValueError("all target artifacts must have engineering_role='design'")
        run = TlrRun(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            status="pending",
            config=request.model_dump(mode="json"),
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    def _require_providers(self) -> tuple[LLMProvider, EmbeddingProvider]:
        missing = [
            name
            for name, value in (
                ("LLM", self.llm),
                ("embedding", self.embedding),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(f"TLR execution requires configured providers: {', '.join(missing)}")
        return self.llm, self.embedding  # type: ignore[return-value]

    async def _clear_derived(self, run_id: str) -> None:
        for model in (
            TraceLink,
            Candidate,
            TraceUnit,
            RetrievalProjection,
            SemanticAnnotation,
        ):
            await self.session.execute(delete(model).where(model.run_id == run_id))

    async def execute(self, run_id: str) -> TlrRun:
        run = await self.session.get(TlrRun, run_id)
        if run is None:
            raise ValueError("run not found")
        if run.status == "completed" and run.result_status in {
            ResultValidity.LEGACY,
            ResultValidity.VALID,
        }:
            return run
        if run.status == "pending":
            await self._clear_derived(run.id)
            run.stage = "semantic_annotation"
            run.metrics = {}
        run.status = "running"
        run.result_status = ResultValidity.NOT_EVALUATED
        run.error = None
        run.finished_at = None
        await self.session.commit()
        try:
            return await self._execute(run)
        except asyncio.CancelledError:
            await self.session.rollback()
            interrupted = await self.session.get(TlrRun, run_id)
            if interrupted is not None:
                interrupted.status = "interrupted"
                interrupted.error = "executor_interrupted: persisted checkpoints retained for resume"
                await self.session.commit()
            raise
        except Exception as exc:
            await self.session.rollback()
            failed = await self.session.get(TlrRun, run_id)
            if failed is not None:
                failed.status = "failed"
                failed.result_status = ResultValidity.INVALID
                failed.error = f"{type(exc).__name__}: {exc}"
                failed.finished_at = datetime.now(UTC)
                await self.session.commit()
            raise
        finally:
            # Cancellation of the parent HTTP/job coroutine must cancel every
            # outstanding Fine call too; create_task is not structured ownership.
            children = getattr(self, "_validation_tasks", [])
            for child in children:
                if not child.done():
                    child.cancel()
            if children:
                await asyncio.gather(*children, return_exceptions=True)

    async def _execute(self, run: TlrRun) -> TlrRun:
        llm, embedding = self._require_providers()
        config = RunCreate.model_validate(run.config)
        source_artifacts = list(
            await self.session.scalars(
                select(Artifact).where(Artifact.id.in_(config.source_artifact_ids))
            )
        )
        target_artifacts = list(
            await self.session.scalars(
                select(Artifact).where(Artifact.id.in_(config.target_artifact_ids))
            )
        )
        artifacts = source_artifacts + target_artifacts
        artifact_map = {item.id: item for item in artifacts}
        nodes = list(
            await self.session.scalars(
                select(ContentNode).where(ContentNode.artifact_id.in_(artifact_map))
            )
        )
        node_map = {item.id: item for item in nodes}
        relations = list(
            await self.session.scalars(
                select(ContentRelation).where(ContentRelation.artifact_id.in_(artifact_map))
            )
        )
        eligible_nodes = [
            node
            for node in nodes
            if node.node_type in {"paragraph", "table"}
            and node_content(node, node_map, relations).strip()
        ]
        eligible_nodes.sort(key=lambda item: (item.artifact_id, item.reading_order))

        # Stage 1: role and synopsis are produced atomically in one model call.
        # Batch commits make thousands of calls resumable.
        run.stage = "semantic_annotation"
        await self.session.commit()
        annotations = list(
            await self.session.scalars(
                select(SemanticAnnotation).where(SemanticAnnotation.run_id == run.id)
            )
        )
        annotation_by_node = {item.node_id: item for item in annotations}
        if any(item.prompt_version != SEMANTIC_ANNOTATION_PROMPT_VERSION for item in annotations):
            raise ValueError("legacy annotation checkpoint cannot resume under joint annotation; create a new TLR run")
        annotator = SemanticAnnotator(
            llm, generation_version=config.options.generation_version, settings=Settings()
        )
        created_since_checkpoint = 0
        for node in eligible_nodes:
            if node.id in annotation_by_node:
                continue
            annotation = await annotator.annotate(
                run.id, artifact_map[node.artifact_id], node, node_map, relations
            )
            annotations.append(annotation)
            annotation_by_node[node.id] = annotation
            self.session.add(annotation)
            created_since_checkpoint += 1
            if created_since_checkpoint >= 50:
                await self.session.commit()
                created_since_checkpoint = 0
        if created_since_checkpoint:
            await self.session.commit()

        # Stage 2: projections, units and embeddings are one checkpoint.  A
        # failed transaction restarts this stage without repeating annotation.
        run.stage = "retrieval_projection"
        source_ids = {item.id for item in source_artifacts}
        target_ids = {item.id for item in target_artifacts}
        projections = list(
            await self.session.scalars(
                select(RetrievalProjection).where(RetrievalProjection.run_id == run.id)
            )
        )
        units = list(
            await self.session.scalars(select(TraceUnit).where(TraceUnit.run_id == run.id))
        )
        if not projections:
            # No partially committed projection stage is possible: the stage
            # is committed atomically below.
            for node in eligible_nodes:
                annotation = annotation_by_node[node.id]
                artifact = artifact_map[node.artifact_id]
                path, raw_text = raw_projection(artifact, node, node_map, relations)
                raw = RetrievalProjection(
                    run_id=run.id,
                    node_id=node.id,
                    annotation_id=annotation.id,
                    channel="raw",
                    document_type=document_type(artifact),
                    heading_path=path,
                    text=raw_text,
                )
                projections.append(raw)
                self.session.add(raw)
                if annotation.synopsis_status == "completed" and annotation.semantic_synopsis:
                    synopsis = RetrievalProjection(
                        run_id=run.id,
                        node_id=node.id,
                        annotation_id=annotation.id,
                        channel="synopsis",
                        document_type=document_type(artifact),
                        heading_path=path,
                        text=synopsis_projection(
                            document_type(artifact), path, annotation.semantic_synopsis
                        ),
                    )
                    projections.append(synopsis)
                    self.session.add(synopsis)
            await self.session.flush()
            projection_by_key = {(item.node_id, item.channel): item for item in projections}
            for node in eligible_nodes:
                annotation = annotation_by_node[node.id]
                if annotation.role not in {"primary", "uncertain"}:
                    continue
                side = "source" if node.artifact_id in source_ids else "target"
                if node.artifact_id not in source_ids | target_ids:
                    continue
                synopsis_row = projection_by_key.get((node.id, "synopsis"))
                unit = TraceUnit(
                    run_id=run.id,
                    artifact_id=node.artifact_id,
                    node_id=node.id,
                    annotation_id=annotation.id,
                    raw_projection_id=projection_by_key[(node.id, "raw")].id,
                    synopsis_projection_id=synopsis_row.id if synopsis_row else None,
                    side=side,
                    semantic_role=annotation.role,
                    ordinal=node.reading_order,
                )
                units.append(unit)
                self.session.add(unit)
            await self.session.flush()
        projection_map = {item.id: item for item in projections}
        source_units = [item for item in units if item.side == "source"]
        target_units = [item for item in units if item.side == "target"]

        run.stage = "dense_index"
        preflight = {
            "raw_embedding": await self._embed_units(
                source_units, target_units, projection_map, embedding, "raw"
            )
        }
        run.metrics = {**(run.metrics or {}), "input_preflight": preflight}
        run.stage = "hybrid_retrieval"
        await self.session.commit()

        # Stage 3: retrieve raw projections, fuse the two independent rankings
        # with unweighted RRF, take configurable Top-K seeds, and expand one
        # structural hop. Every promoted structural node remains an independent
        # candidate for fine validation; supporting context never changes the
        # target node.
        candidate_rows = list(
            await self.session.scalars(select(Candidate).where(Candidate.run_id == run.id))
        )
        dense: dict = {}
        bm25: dict = {}
        if not candidate_rows:
            dense_retriever = DenseRetriever(self.session)
            dense = await dense_retriever.retrieve(
                source_units,
                target_units,
                projection_map,
                channel="raw",
                top_k=config.options.dense_top_k,
            )
            bm25 = BM25Retriever(ChineseTokenizer(config.options.tokenizer_user_terms)).retrieve(
                source_units,
                target_units,
                projection_map,
                top_k=config.options.bm25_top_k,
            )

            fused = union_candidates(
                source_units,
                target_units,
                dense,
                bm25,
                rrf_k=config.options.rrf_k,
            )
            grouped_fused: dict[str, list[HybridCandidate]] = defaultdict(list)
            for candidate in fused:
                grouped_fused[candidate.source.id].append(candidate)
            seeds = [
                candidate
                for source in source_units
                for candidate in sorted(
                    grouped_fused.get(source.id, []), key=lambda item: item.fused_rank or 10**9
                )[: config.options.fine_top_k]
            ]
            target_relations = [item for item in relations if item.artifact_id in target_ids]
            candidates = structural_expand(
                seeds,
                target_units,
                node_map,
                target_relations,
                sibling_window=config.options.sibling_window,
            )
            grouped_candidates: dict[str, list[HybridCandidate]] = defaultdict(list)
            for item in candidates:
                grouped_candidates[item.source.id].append(item)
            fine_order: dict[tuple[str, str], int] = {}
            for source in source_units:
                group = sorted(
                    grouped_candidates.get(source.id, []),
                    key=lambda item: (
                        0 if item.fused_rank is not None else 1,
                        item.fused_rank or 10**9,
                        item.target.ordinal,
                        item.target.id,
                    ),
                )
                for order, item in enumerate(group, start=1):
                    fine_order[(item.source.id, item.target.id)] = order
            for item in candidates:
                target_node = node_map[item.target.node_id]
                order = fine_order[(item.source.id, item.target.id)]
                row = Candidate(
                    run_id=run.id,
                    source_unit_id=item.source.id,
                    target_unit_id=item.target.id,
                    dense_rank=item.dense_rank,
                    dense_score=item.dense_score,
                    synopsis_dense_rank=item.synopsis_dense_rank,
                    synopsis_dense_score=item.synopsis_dense_score,
                    bm25_rank=item.bm25_rank,
                    bm25_score=item.bm25_score,
                    rrf_score=item.rrf_score,
                    fused_rank=item.fused_rank,
                    retrieval_sources=sorted(item.retrieval_sources),
                    structural_reasons=item.structural_reasons,
                    reranker_rank=None,
                    reranker_score=None,
                    diagnostics={
                        "heading_path": heading_path(target_node, node_map),
                        "node_type": target_node.node_type,
                        "preview": node_content(target_node, node_map, relations)[:500],
                        "candidate_kind": (
                            "structural_independent"
                            if item.retrieval_sources == {"structure"}
                            else "retrieval"
                        ),
                        "selection_stage": "rrf_top_k_and_one_hop_structure",
                        "fine_order": order,
                    },
                    execution_status=ExecutionStatus.PENDING,
                    validation_status=ValidationStatus.NOT_CHECKED,
                    attempt_count=0,
                )
                candidate_rows.append(row)
                self.session.add(row)
            run.metrics = {
                **(run.metrics or {}),
                "dense_hits": len(dense),
                "bm25_hits": len(bm25),
                "rrf_seed_candidates": len(seeds),
                "fine_top_k": config.options.fine_top_k,
                "hybrid_and_expanded_candidates": len(candidates),
                "reranker_enabled": False,
            }
            run.stage = "fine_validation"
            await self.session.commit()

        # Stage 4: every pair is an independently resumable checkpoint. LLM work
        # runs through a bounded worker pool; each completed pair is committed as
        # soon as it finishes instead of waiting for the slowest batch member.
        units = list(
            await self.session.scalars(select(TraceUnit).where(TraceUnit.run_id == run.id))
        )
        unit_by_id = {item.id: item for item in units}
        validator = FineTraceValidator(
            llm, max_tokens=config.options.fine_max_tokens, settings=Settings()
        )
        pending_rows = [
            row
            for row in sorted(
                candidate_rows,
                key=lambda item: (
                    item.source_unit_id,
                    int((item.diagnostics or {}).get("fine_order", 10**9)),
                ),
            )
            if not (
                row.execution_status == ExecutionStatus.SUCCEEDED
                and row.validation_status == ValidationStatus.VALID
            )
        ]
        semaphore = asyncio.Semaphore(config.options.fine_concurrency)
        work: list[tuple[Candidate, TraceUnit, TraceUnit, object]] = []
        for row in pending_rows:
            source_unit = unit_by_id[row.source_unit_id]
            target_unit = unit_by_id[row.target_unit_id]
            source_node = node_map[source_unit.node_id]
            target_node = node_map[target_unit.node_id]
            package = assemble_evidence(
                source_node,
                target_node,
                artifact_map[source_node.artifact_id],
                artifact_map[target_node.artifact_id],
                node_map,
                relations,
                sibling_window=config.options.sibling_window,
            )
            row.execution_status = ExecutionStatus.RUNNING
            row.validation_status = ValidationStatus.NOT_CHECKED
            row.attempt_count = (row.attempt_count or 0) + 1
            row.error_category = row.error_code = row.last_error = None
            row.validator_provider = llm.name
            row.validator_model = llm.model
            row.validator_model_version = llm.model_version
            row.prompt_version = FINE_TRACE_PROMPT_VERSION
            row.prompt_hash = prompt_hash(FINE_TRACE_SYSTEM_PROMPT)
            row.schema_version = "fine-trace-v2"
            row.schema_hash = schema_hash(FineTraceModelOutput.model_json_schema())
            row.input_fingerprint = fingerprint_payload(
                package.as_prompt(),
                llm.name,
                llm.model,
                llm.model_version,
                FINE_TRACE_PROMPT_VERSION,
                row.prompt_hash,
                row.schema_hash,
                config.options.fine_max_tokens,
            )
            work.append((row, source_unit, target_unit, package))
        await self.session.commit()

        async def validate_one(item):
            row, source_unit, target_unit, package = item
            async with semaphore:
                try:
                    result = await validator.validate(package, node_map, relations)
                    return row.id, source_unit, target_unit, package, result, None
                except Exception as exc:
                    return row.id, source_unit, target_unit, package, None, exc

        tasks = [asyncio.create_task(validate_one(item)) for item in work]
        self._validation_tasks = tasks
        for completed in asyncio.as_completed(tasks):
            row_id, source_unit, target_unit, package, result, error = await completed
            row = await self.session.get(Candidate, row_id)
            if error is not None:
                descriptor = classify_exception(error)
                row.execution_status = ExecutionStatus.FAILED
                row.validation_status = (
                    ValidationStatus.INVALID
                    if descriptor.category.value in {"structured_output", "evidence_validation"}
                    else ValidationStatus.NOT_CHECKED
                )
                row.error_category = descriptor.category
                row.error_code = descriptor.code
                row.last_error = descriptor.message[:4000]
                row.final_relation = None
                row.evidence_source_node_ids = []
                row.evidence = None
                row.explanation = ""
                await self.session.commit()
                continue

            row.execution_status = ExecutionStatus.SUCCEEDED
            row.validation_status = ValidationStatus.VALID
            row.final_relation = result.relation
            row.evidence_source_node_ids = [item.source_node_id for item in result.evidence]
            row.evidence = {"quotes": [item.model_dump() for item in result.evidence]}
            row.explanation = result.explanation
            row.diagnostics = {
                **(row.diagnostics or {}),
                "supporting_context": [item.__dict__ for item in package.supporting_context],
                "validation": result.diagnostics,
            }
            if result.relation == "direct":
                existing_link = await self.session.scalar(
                    select(TraceLink).where(TraceLink.candidate_id == row.id)
                )
                if existing_link is None:
                    self.session.add(
                        TraceLink(
                            run_id=run.id,
                            source_artifact_id=source_unit.artifact_id,
                            target_artifact_id=target_unit.artifact_id,
                            source_node_id=source_unit.node_id,
                            target_node_id=target_unit.node_id,
                            candidate_id=row.id,
                            relation="direct",
                            evidence_source_node_ids=row.evidence_source_node_ids,
                            evidence=row.evidence,
                            explanation=result.explanation,
                            validator_provider=llm.name,
                            validator_model=llm.model,
                            validator_model_version=llm.model_version,
                            prompt_version=FINE_TRACE_PROMPT_VERSION,
                        )
                    )
            await self.session.commit()

        annotations = list(
            await self.session.scalars(
                select(SemanticAnnotation).where(SemanticAnnotation.run_id == run.id)
            )
        )
        candidate_rows = list(
            await self.session.scalars(select(Candidate).where(Candidate.run_id == run.id))
        )
        links = list(
            await self.session.scalars(select(TraceLink).where(TraceLink.run_id == run.id))
        )
        annotation_roles = Counter(item.role for item in annotations)
        run.metrics = {
            **(run.metrics or {}),
            "canonical_nodes_read": len(nodes),
            "annotations": len(annotations),
            "annotation_roles": dict(annotation_roles),
            "synopsis_failures": sum(item.synopsis_status == "failed" for item in annotations),
            "pictures_skipped": sum(item.node_type == "picture" for item in nodes),
            "source_units": len(source_units),
            "target_units": len(target_units),
            "fine_screened": sum(item.final_relation is not None for item in candidate_rows),
            "fine_execution_counts": dict(
                Counter(item.execution_status for item in candidate_rows)
            ),
            "fine_validation_counts": dict(
                Counter(item.validation_status for item in candidate_rows)
            ),
            "fine_concurrency": config.options.fine_concurrency,
            "trace_links": len(links),
            "judgement_counts": dict(
                Counter(item.final_relation for item in candidate_rows if item.final_relation)
            ),
            "embedding_provider": embedding.name,
            "embedding_model": embedding.model,
            "embedding_model_version": embedding.model_version,
            "reranker_enabled": False,
            "bm25": "bm25s",
            "tokenizer": "jieba-py",
        }
        failed_pairs = [
            item for item in candidate_rows if item.execution_status == ExecutionStatus.FAILED
        ]
        succeeded_pairs = [
            item
            for item in candidate_rows
            if item.execution_status == ExecutionStatus.SUCCEEDED
            and item.validation_status == ValidationStatus.VALID
        ]
        run.result_status = (
            ResultValidity.VALID
            if not failed_pairs
            else ResultValidity.PARTIAL
            if succeeded_pairs
            else ResultValidity.INCOMPLETE
        )
        run.status, run.stage, run.finished_at = "completed", "completed", datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def _embed_units(
        self,
        sources: list[TraceUnit],
        targets: list[TraceUnit],
        projections: dict[str, RetrievalProjection],
        provider: EmbeddingProvider,
        channel: str,
    ) -> dict:
        def projection_id(unit: TraceUnit) -> str | None:
            value = unit.raw_projection_id if channel == "raw" else unit.synopsis_projection_id
            return value

        source_rows = [
            projections[value] for item in sources if (value := projection_id(item)) is not None
        ]
        target_rows = [
            projections[value] for item in targets if (value := projection_id(item)) is not None
        ]
        all_rows = source_rows + target_rows
        if all_rows and all(item.embedding is not None for item in all_rows):
            mismatched = [
                item.id
                for item in all_rows
                if item.embedding_provider != provider.name
                or item.embedding_model != provider.model
                or item.embedding_model_version != provider.model_version
            ]
            if mismatched:
                raise ValueError(
                    "persisted embeddings were produced by a different provider/model; "
                    "create a new run instead of mixing results"
                )
            return {
                "status": "reused_persisted_embeddings",
                "queries": len(source_rows),
                "documents": len(target_rows),
            }
        source_report = await provider.query_input_length_report(
            [item.text for item in source_rows], DENSE_TASK_INSTRUCTION
        )
        target_report = await provider.input_length_report([item.text for item in target_rows])
        self._reject_oversize(source_report, f"embedding_{channel}_queries")
        self._reject_oversize(target_report, f"embedding_{channel}_documents")
        source_vectors = await provider.encode_queries(
            [item.text for item in source_rows], DENSE_TASK_INSTRUCTION
        )
        target_vectors = await provider.encode_documents([item.text for item in target_rows])
        if len(source_vectors) != len(source_rows) or len(target_vectors) != len(target_rows):
            raise ValueError("embedding provider returned a different number of vectors")
        for row, vector in zip(source_rows, source_vectors, strict=True):
            row.embedding = vector
            row.embedding_provider = provider.name
            row.embedding_model = provider.model
            row.embedding_model_version = provider.model_version
        for row, vector in zip(target_rows, target_vectors, strict=True):
            row.embedding = vector
            row.embedding_provider = provider.name
            row.embedding_model = provider.model
            row.embedding_model_version = provider.model_version
        return {
            "queries": self._compact_preflight(source_report),
            "documents": self._compact_preflight(target_report),
        }

    @staticmethod
    def _reject_oversize(report: dict, stage: str) -> None:
        if report.get("oversize_indexes"):
            raise ValueError(
                f"{stage}_input_oversize: silent truncation is disabled; "
                f"indexes={report['oversize_indexes']}, max_tokens={report.get('max_tokens')}"
            )

    @staticmethod
    def _compact_preflight(report: dict) -> dict:
        return {key: value for key, value in report.items() if key != "token_lengths"}
