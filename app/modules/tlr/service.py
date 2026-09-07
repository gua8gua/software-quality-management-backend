import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.model_config.runtime import resolve_tlr_models
from app.modules.tlr.diagnostics import PreprocessingFailure, failure_detail, preflight
from app.modules.tlr.models import (
    TlrArtifact,
    TlrCandidate,
    TlrDataset,
    TlrElement,
    TlrEvaluation,
    TlrHierarchyNode,
    TlrLink,
    TlrProject,
    TlrRun,
)
from app.modules.tlr.pipeline import (
    PROMPT,
    PROMPT_VERSION,
    ClassificationOutputError,
    LissaRetriever,
    PairClassifier,
    PythonRetriever,
    digest,
    validate_vectors,
)
from app.modules.tlr.preprocessing import preprocess_typed
from app.modules.tlr.repository import TlrRepository
from app.modules.tlr.schemas import DatasetInput, EvaluationInput, RunInput, RunOptions
from app.providers.embedding.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider

BASELINE_COMMIT = "a8e652f29bbdcc4bdcd3d98cd85aa4a37cb65480"


class TlrService:
    def __init__(
        self,
        session: AsyncSession,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
        settings: Settings,
    ):
        self.session = session
        self.repo = TlrRepository(session)
        self.embedding = embedding
        self.classifier = PairClassifier(llm)
        self.architecture_llm = llm
        self.model_snapshot: dict = {}
        self.settings = settings

    async def import_dataset(self, request: DatasetInput, *, commit: bool = True):
        # Immutable snapshot; subsequent versions are separate rows, never overwritten.
        project_key = (request.tenant_id, request.project_id)
        if await self.session.get(TlrProject, project_key) is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(
                        TlrProject(
                            tenant_id=request.tenant_id,
                            id=request.project_id,
                            name=request.project_id,
                        )
                    )
                    await self.session.flush()
            except IntegrityError:
                if await self.session.get(TlrProject, project_key) is None:
                    raise
        canonical = json.dumps(request.model_dump(), ensure_ascii=False, sort_keys=True)
        dataset = TlrDataset(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            version=request.version,
            provenance=request.provenance,
            digest=digest(canonical),
        )
        self.session.add(dataset)
        await self.session.flush()
        artifact_rows = {}
        for artifact in request.artifacts:
            row = TlrArtifact(
                dataset_id=dataset.id, **artifact.model_dump(), sha256=digest(artifact.content)
            )
            self.session.add(row)
            artifact_rows[artifact.external_id] = row
        await self.session.flush()
        await self._persist_hierarchy(dataset.id, request, artifact_rows)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        await self.session.refresh(dataset)
        return dataset

    async def _persist_hierarchy(self, dataset_id, request, artifacts):
        """Persist input hierarchy; legacy structure metadata is converted once at import."""
        if request.hierarchy:
            definitions = request.hierarchy
        else:
            definitions = []
            for ordinal, artifact in enumerate(request.artifacts):
                structure = artifact.structure or {}
                parents = [p for p in structure.get("parent_ids", []) if p in artifacts]
                legacy_structure = (
                    artifact.kind == "package"
                    or structure.get("content_status") == "reference_only"
                )
                definitions.append(
                    {
                        "node_key": f"artifact:{artifact.external_id}",
                        "parent_key": f"artifact:{parents[0]}" if parents else None,
                        "artifact_external_id": None if legacy_structure else artifact.external_id,
                        "title": structure.get("title")
                        or structure.get("original_id")
                        or artifact.external_id,
                        "node_type": structure.get("original_type")
                        or ("group" if legacy_structure else "artifact"),
                        "ordinal": ordinal,
                        "metadata": {
                            "source": "artifact.structure",
                            "legacy_external_id": artifact.external_id,
                            "all_parent_ids": parents,
                            "content_status": structure.get("content_status"),
                        },
                    }
                )
        nodes = {}
        for definition in definitions:
            value = definition.model_dump() if hasattr(definition, "model_dump") else definition
            artifact_external_id = value.get("artifact_external_id")
            row = TlrHierarchyNode(
                dataset_id=dataset_id,
                artifact_id=(artifacts[artifact_external_id].id if artifact_external_id else None),
                node_key=value["node_key"],
                title=value["title"],
                node_type=value.get("node_type", "group"),
                ordinal=value.get("ordinal", 0),
                metadata_json=value.get("metadata", {}),
            )
            self.session.add(row)
            nodes[value["node_key"]] = (row, value.get("parent_key"))
        await self.session.flush()
        for row, parent_key in nodes.values():
            row.parent_id = nodes[parent_key][0].id if parent_key else None
        await self.session.flush()

    async def create_run(self, request: RunInput, *, commit: bool = True):
        dataset = await self.repo.dataset(request.dataset_id, request.tenant_id, request.project_id)
        artifacts = await self.repo.artifacts(dataset.id)
        available = {
            a.external_id
            for a in artifacts
            if a.kind != "package" and (a.structure or {}).get("content_status") != "reference_only"
        }
        if not set(request.source_ids + request.target_ids) <= available:
            raise AppError(ErrorCode.DATA_INVALID, "所选制品不属于指定数据快照")
        run = TlrRun(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            dataset_id=dataset.id,
            config=request.model_dump(),
            manifest={"schema_version": "1.0", "dataset_digest": dataset.digest},
        )
        self.session.add(run)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        await self.session.refresh(run)
        return run

    async def execute(self, run_id: str, tenant_id: str, project_id: str):
        run = await self.repo.run(run_id, tenant_id, project_id)
        # Atomic claim prevents simultaneous execution even across application workers.
        claimed = await self.session.execute(
            update(TlrRun)
            .where(TlrRun.id == run_id, TlrRun.status == "pending")
            .values(status="running", stage="preprocessing")
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise AppError(ErrorCode.DATA_DUPLICATE, "该运行已执行或正在执行；重试请新建运行")
        await self.session.commit()
        await self.session.refresh(run)
        try:
            embedding, classifier_llm, architecture_llm, snapshot = await resolve_tlr_models(
                self.session, self.settings, tenant_id, self.embedding, self.classifier.llm
            )
            self.embedding = embedding
            self.classifier = PairClassifier(classifier_llm)
            self.architecture_llm = architecture_llm
            self.model_snapshot = snapshot
            preflight(self.embedding, self.classifier.llm)
            await self._pipeline(run)
        except BaseException as exc:
            # A failed classification stays pending, never coerced to a negative.
            detail = failure_detail(exc)
            failure_manifest = {**run.manifest, "failure": detail}
            await self.session.rollback()
            await self.session.execute(
                update(TlrRun)
                .where(TlrRun.id == run_id)
                .values(
                    status="failed",
                    error=detail["message"],
                    manifest=failure_manifest,
                    finished_at=datetime.now(UTC),
                )
            )
            await self.session.commit()
            if not isinstance(exc, Exception):
                raise
            raise AppError(
                ErrorCode.SERVER_ERROR,
                detail["message"],
                data={"run_id": run_id, **detail},
            ) from exc
        return await self.repo.run(run_id, tenant_id, project_id)

    async def resume(self, run_id: str, tenant_id: str, project_id: str):
        """Continue durable pending candidates without rebuilding completed pipeline work."""
        run = await self.repo.run(run_id, tenant_id, project_id)
        candidates = await self.repo.rows(TlrCandidate, run_id)
        if run.stage not in {"classification", "completed_with_errors"} or not any(
            candidate.decision == "pending" for candidate in candidates
        ):
            raise AppError(ErrorCode.DATA_INVALID, "该运行没有可恢复的待判定候选")
        claimed = await self.session.execute(
            update(TlrRun)
            .where(TlrRun.id == run_id, TlrRun.status.in_(["failed", "completed"]))
            .values(status="running", stage="classification", error=None, finished_at=None)
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise AppError(ErrorCode.DATA_DUPLICATE, "该运行正在执行，不能重复恢复")
        await self.session.commit()
        await self.session.refresh(run)
        try:
            embedding, classifier_llm, architecture_llm, snapshot = await resolve_tlr_models(
                self.session, self.settings, tenant_id, self.embedding, self.classifier.llm
            )
            self.embedding = embedding
            self.classifier = PairClassifier(classifier_llm)
            self.architecture_llm = architecture_llm
            self.model_snapshot = snapshot
            preflight(self.embedding, self.classifier.llm)
            await self._resume_classification(run)
        except BaseException as exc:
            detail = failure_detail(exc)
            failure_manifest = {**(run.manifest or {}), "failure": detail}
            await self.session.rollback()
            await self.session.execute(
                update(TlrRun)
                .where(TlrRun.id == run_id)
                .values(
                    status="failed",
                    error=detail["message"],
                    manifest=failure_manifest,
                    finished_at=datetime.now(UTC),
                )
            )
            await self.session.commit()
            if not isinstance(exc, Exception):
                raise
            raise AppError(
                ErrorCode.SERVER_ERROR,
                detail["message"],
                data={"run_id": run_id, **detail},
            ) from exc
        return await self.repo.run(run_id, tenant_id, project_id)

    async def _pipeline(self, run: TlrRun):
        options = RunOptions.model_validate(run.config["options"])
        node_failures: list[dict] = []

        async def record_failure(stage: str, node_type: str, exc: Exception, **identity):
            detail = failure_detail(exc)
            node_failures.append(
                {"stage": stage, "node_type": node_type, **identity, **detail}
            )
            run.manifest = {**run.manifest, "node_failures": node_failures, "partial": True}
            await self.session.commit()

        retriever = (
            PythonRetriever()
            if options.retrieval_backend == "python"
            else LissaRetriever(
                self.settings.tlr_lissa_jar,
                self.settings.tlr_lissa_bridge_dir,
                self.settings.tlr_java_command,
                self.settings.tlr_retrieval_timeout_seconds,
            )
        )
        run.manifest = {
            **run.manifest,
            "implementation": "lissa-backend-adaptation-v1",
            "baseline_commit": BASELINE_COMMIT,
            "retriever": retriever.name,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": digest(PROMPT),
            "prompt": PROMPT,
            "aggregation": "any_positive_element_pair",
            "model_tasks": self.model_snapshot,
            "embedding_model": self.model_snapshot["tlr_embedding"]["model_id"],
            "embedding_base_url": self.model_snapshot["tlr_embedding"]["base_url"],
            "llm_model": self.classifier.llm.model,
            "llm_base_url": self.model_snapshot["tlr_classification"]["base_url"],
            "temperature": 0.0,
        }
        await self.session.commit()
        artifacts = await self.repo.artifacts(run.dataset_id)
        sources, targets = [], []
        for role, destination in [("source", sources), ("target", targets)]:
            selected = set(run.config[f"{role}_ids"])
            for artifact in artifacts:
                if artifact.external_id in selected:
                    try:
                        units = await preprocess_typed(
                            artifact, role, run.id, options, self.architecture_llm
                        )
                    except (ValueError, SyntaxError) as exc:
                        await record_failure(
                            "preprocessing",
                            "artifact",
                            PreprocessingFailure(str(exc)),
                            artifact_id=artifact.id,
                            external_id=artifact.external_id,
                            role=role,
                        )
                        continue
                    destination.extend(units)
                    self.session.add_all(units)
                    await self.session.commit()
                    if len(sources) + len(targets) > self.settings.tlr_max_elements:
                        raise ValueError("TLR element limit exceeded")
        if not sources or not targets:
            raise PreprocessingFailure(
                "预处理后源侧或目标侧没有可分析单元；请检查失败节点、正文和分割策略"
            )
        if len(sources) * min(options.top_k, len(targets)) > self.settings.tlr_max_candidates:
            raise ValueError("TLR candidate limit exceeded")
        if len(sources) * len(targets) > self.settings.tlr_max_comparisons:
            raise ValueError("TLR retrieval comparison limit exceeded")
        self.session.add_all(sources + targets)
        run.counts = {"source_elements": len(sources), "target_elements": len(targets)}
        run.stage = "embedding"
        await self.session.commit()
        elements = sources + targets
        cached = await self._cached_embeddings(run, elements)
        for element in elements:
            cache_key = (element.external_id, element.sha256)
            if cache_key in cached:
                element.embedding = cached[cache_key]
        missing = [element for element in elements if element.embedding is None]
        dimension = next(
            (len(element.embedding) for element in elements if element.embedding), None
        )
        encoded = 0
        embedding_failures = 0
        embedding_skipped = 0
        consecutive_embedding_failures = 0
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            try:
                vectors = await self.embedding.embed([e.content for e in batch])
                dimension = validate_vectors(vectors, len(batch), dimension)
                for element, vector in zip(batch, vectors, strict=True):
                    element.embedding = vector
                    cached[(element.external_id, element.sha256)] = vector
                    encoded += 1
                consecutive_embedding_failures = 0
                await self.session.commit()
                continue
            except Exception:
                # Retry separately so one malformed/unsupported unit does not discard its batch.
                pass
            for index, element in enumerate(batch):
                if consecutive_embedding_failures >= options.max_consecutive_failures:
                    remaining = batch[index:] + missing[start + len(batch) :]
                    for skipped in remaining:
                        skipped.processing = {
                            **(skipped.processing or {}),
                            "failure": {
                                "stage": "embedding",
                                "status": "skipped",
                                "reason": "circuit_open_after_consecutive_failures",
                            },
                        }
                    embedding_skipped += len(remaining)
                    run.manifest = {
                        **run.manifest,
                        "node_failures": node_failures,
                        "partial": True,
                        "embedding_circuit_open": True,
                    }
                    await self.session.commit()
                    break
                try:
                    vectors = await self.embedding.embed([element.content])
                    dimension = validate_vectors(vectors, 1, dimension)
                    element.embedding = vectors[0]
                    cached[(element.external_id, element.sha256)] = vectors[0]
                    encoded += 1
                    consecutive_embedding_failures = 0
                    await self.session.commit()
                except Exception as exc:
                    embedding_failures += 1
                    consecutive_embedding_failures += 1
                    element.processing = {
                        **(element.processing or {}),
                        "failure": {
                            "stage": "embedding",
                            "status": "failed",
                            **failure_detail(exc),
                        },
                    }
                    await record_failure(
                        "embedding",
                        "element",
                        exc,
                        element_id=element.id,
                        artifact_id=element.artifact_id,
                        external_id=element.external_id,
                        role=element.role,
                    )
            if consecutive_embedding_failures >= options.max_consecutive_failures:
                break
        active_sources = [element for element in sources if element.embedding is not None]
        active_targets = [element for element in targets if element.embedding is not None]
        if not active_sources or not active_targets:
            raise ValueError(
                "Embedding 后源侧或目标侧没有可检索单元；请检查失败节点和模型服务"
            )
        run.manifest = {
            **run.manifest,
            "embedding_dimension": dimension,
            "embedding_cache": {
                "reused": len(elements) - len(missing),
                "encoded": encoded,
                "key": "project + artifact_external_id + content_sha256 + embedding_endpoint/model",
            },
        }
        if embedding_failures or embedding_skipped:
            run.counts = {
                **run.counts,
                "embedding_failed": embedding_failures,
                "embedding_skipped": embedding_skipped,
            }
        run.stage = "retrieval"
        if isinstance(retriever, LissaRetriever):
            run.manifest = {**run.manifest, "runtime_artifacts": await retriever.fingerprint()}
        await self.session.commit()

        rows = await retriever.retrieve(active_sources, active_targets, options.top_k)
        candidates = [
            TlrCandidate(
                run_id=run.id, source_element_id=s, target_element_id=t, rank=rank, similarity=score
            )
            for s, t, rank, score in rows
        ]
        self.session.add_all(candidates)
        run.counts = {**run.counts, "candidates": len(candidates), "classified": 0}
        run.stage = "classification"
        await self.session.commit()
        by_id = {e.id: e for e in elements}
        classification_failures = 0
        classification_skipped = 0
        consecutive_classification_failures = 0
        for index, candidate in enumerate(candidates, 1):
            try:
                decision = await self.classifier.classify(
                    by_id[candidate.source_element_id], by_id[candidate.target_element_id]
                )
            except ClassificationOutputError as exc:
                candidate.evidence = {
                    "validation_status": "invalid",
                    "raw_response": exc.raw_response,
                    **failure_detail(exc),
                }
                classification_failures += 1
                consecutive_classification_failures += 1
                await record_failure(
                    "classification",
                    "candidate",
                    exc,
                    candidate_id=candidate.id,
                    source_element_id=candidate.source_element_id,
                    target_element_id=candidate.target_element_id,
                )
            except Exception as exc:
                candidate.evidence = {
                    "validation_status": "failed",
                    **failure_detail(exc),
                }
                classification_failures += 1
                consecutive_classification_failures += 1
                await record_failure(
                    "classification",
                    "candidate",
                    exc,
                    candidate_id=candidate.id,
                    source_element_id=candidate.source_element_id,
                    target_element_id=candidate.target_element_id,
                )
            else:
                candidate.decision = "related" if decision.related else "unrelated"
                candidate.evidence = decision.model_dump()
                run.counts = {**run.counts, "classified": run.counts["classified"] + 1}
                consecutive_classification_failures = 0
                await self.session.commit()
            if consecutive_classification_failures >= options.max_consecutive_failures:
                remaining = candidates[index:]
                for skipped in remaining:
                    skipped.evidence = {
                        "validation_status": "skipped",
                        "reason": "circuit_open_after_consecutive_failures",
                    }
                classification_skipped = len(remaining)
                run.manifest = {
                    **run.manifest,
                    "node_failures": node_failures,
                    "partial": True,
                    "classification_circuit_open": True,
                }
                await self.session.commit()
                break
        if classification_failures or classification_skipped:
            run.counts = {
                **run.counts,
                "classification_failed": classification_failures,
                "classification_skipped": classification_skipped,
            }
        if candidates and run.counts["classified"] == 0:
            raise RuntimeError("所有候选判定均失败；请检查模型服务和结构化输出能力")
        run.stage = "aggregation"
        await self.session.commit()
        link_count = await self._aggregate_persisted_links(run)
        run.counts = {**run.counts, "links": link_count}
        partial = bool(node_failures or embedding_skipped or classification_skipped)
        run.manifest = {
            **run.manifest,
            "node_failures": node_failures,
            "partial": partial,
            "outcome": "partial" if partial else "completed",
        }
        run.status = "completed"
        run.stage = "completed_with_errors" if partial else "completed"
        run.finished_at = datetime.now(UTC)
        await self.session.commit()

    async def _aggregate_persisted_links(self, run: TlrRun) -> int:
        """Aggregate durable candidate decisions so partial runs can be finalized safely."""
        candidates = await self.repo.rows(TlrCandidate, run.id)
        elements = await self.repo.rows(TlrElement, run.id)
        by_id = {element.id: element for element in elements}
        grouped = defaultdict(list)
        for candidate in candidates:
            if candidate.decision != "related":
                continue
            source = by_id.get(candidate.source_element_id)
            target = by_id.get(candidate.target_element_id)
            if source is None or target is None:
                continue
            grouped[(source.artifact_id, target.artifact_id)].append(candidate.id)

        existing = {
            (link.source_artifact_id, link.target_artifact_id): link
            for link in await self.repo.rows(TlrLink, run.id)
        }
        for (source, target), evidence in grouped.items():
            link = existing.get((source, target))
            if link is None:
                self.session.add(
                    TlrLink(
                        run_id=run.id,
                        source_artifact_id=source,
                        target_artifact_id=target,
                        evidence_candidate_ids=evidence,
                    )
                )
            else:
                link.evidence_candidate_ids = evidence
        return len(grouped)

    async def _resume_classification(self, run: TlrRun) -> None:
        options = RunOptions.model_validate(run.config["options"])
        candidates = await self.repo.rows(TlrCandidate, run.id)
        elements = await self.repo.rows(TlrElement, run.id)
        by_id = {element.id: element for element in elements}
        pending = [candidate for candidate in candidates if candidate.decision == "pending"]
        attempt_failures: list[dict] = []
        consecutive_failures = 0

        for index, candidate in enumerate(pending, 1):
            try:
                decision = await self.classifier.classify(
                    by_id[candidate.source_element_id], by_id[candidate.target_element_id]
                )
            except ClassificationOutputError as exc:
                candidate.evidence = {
                    "validation_status": "invalid",
                    "raw_response": exc.raw_response,
                    **failure_detail(exc),
                }
                consecutive_failures += 1
                attempt_failures.append(
                    {
                        "stage": "classification",
                        "node_type": "candidate",
                        "candidate_id": candidate.id,
                        "source_element_id": candidate.source_element_id,
                        "target_element_id": candidate.target_element_id,
                        **failure_detail(exc),
                    }
                )
                await self.session.commit()
            except Exception as exc:
                candidate.evidence = {
                    "validation_status": "failed",
                    **failure_detail(exc),
                }
                consecutive_failures += 1
                attempt_failures.append(
                    {
                        "stage": "classification",
                        "node_type": "candidate",
                        "candidate_id": candidate.id,
                        "source_element_id": candidate.source_element_id,
                        "target_element_id": candidate.target_element_id,
                        **failure_detail(exc),
                    }
                )
                await self.session.commit()
            else:
                candidate.decision = "related" if decision.related else "unrelated"
                candidate.evidence = decision.model_dump()
                consecutive_failures = 0
                await self.session.commit()

            if consecutive_failures >= options.max_consecutive_failures:
                for skipped in pending[index:]:
                    skipped.evidence = {
                        "validation_status": "skipped",
                        "reason": "circuit_open_after_consecutive_failures",
                    }
                await self.session.commit()
                break

        classified = sum(candidate.decision != "pending" for candidate in candidates)
        unresolved = [candidate for candidate in candidates if candidate.decision == "pending"]
        failed = sum(
            (candidate.evidence or {}).get("validation_status") in {"invalid", "failed"}
            for candidate in unresolved
        )
        skipped = sum(
            (candidate.evidence or {}).get("validation_status") == "skipped"
            for candidate in unresolved
        )
        if candidates and classified == 0:
            raise RuntimeError("所有候选判定均失败；请检查模型服务和结构化输出能力")

        run.stage = "aggregation"
        await self.session.commit()
        link_count = await self._aggregate_persisted_links(run)
        previous_manifest = dict(run.manifest or {})
        recovery = dict(previous_manifest.get("recovery") or {})
        run.counts = {
            **(run.counts or {}),
            "candidates": len(candidates),
            "classified": classified,
            "classification_failed": failed,
            "classification_skipped": skipped,
            "links": link_count,
        }
        run.manifest = {
            **previous_manifest,
            "node_failures": attempt_failures,
            "partial": bool(unresolved),
            "outcome": "partial" if unresolved else "completed",
            "recovery": {
                **recovery,
                "attempts": int(recovery.get("attempts", 0)) + 1,
                "last_resumed_at": datetime.now(UTC).isoformat(),
                "remaining_candidates": len(unresolved),
            },
        }
        run.manifest.pop("failure", None)
        run.manifest.pop("termination", None)
        run.status = "completed"
        run.stage = "completed_with_errors" if unresolved else "completed"
        run.error = None
        run.finished_at = datetime.now(UTC)
        await self.session.commit()

    async def _cached_embeddings(
        self, run: TlrRun, elements: list[TlrElement]
    ) -> dict[tuple[str, str], list]:
        """Reuse exact-content vectors from this tenant when the embedding endpoint/model match."""
        hashes = {element.sha256 for element in elements}
        if not hashes:
            return {}
        current = self.model_snapshot["tlr_embedding"]
        cached: dict[tuple[str, str], list] = {}
        hash_list = list(hashes)
        for start in range(0, len(hash_list), 500):
            rows = await self.session.execute(
                select(TlrElement, TlrRun)
                .join(TlrRun, TlrElement.run_id == TlrRun.id)
                .where(
                    TlrRun.tenant_id == run.tenant_id,
                    TlrRun.project_id == run.project_id,
                    TlrRun.id != run.id,
                    TlrElement.sha256.in_(hash_list[start : start + 500]),
                    TlrElement.embedding.is_not(None),
                )
                .order_by(TlrRun.created_at.desc())
            )
            for element, previous_run in rows:
                previous = (
                    (previous_run.manifest or {})
                    .get("model_tasks", {})
                    .get("tlr_embedding", {})
                )
                same_model = (
                    previous.get("model_id") == current.get("model_id")
                    and str(previous.get("base_url", "")).rstrip("/")
                    == str(current.get("base_url", "")).rstrip("/")
                )
                vector = element.embedding
                cache_key = (element.external_id, element.sha256)
                if same_model and cache_key not in cached and isinstance(vector, list) and vector:
                    cached[cache_key] = vector
        return cached

    async def evaluate(self, run_id, tenant_id, project_id, request: EvaluationInput):
        run = await self.repo.run(run_id, tenant_id, project_id)
        if run.status != "completed":
            raise AppError(ErrorCode.DATA_INVALID, "仅能评测已完成运行")
        gold = {(g.source_id, g.target_id) for g in request.gold_links}
        if any(
            s not in run.config["source_ids"] or t not in run.config["target_ids"] for s, t in gold
        ):
            raise AppError(ErrorCode.DATA_INVALID, "金标准包含所选制品范围外的链接")
        artifacts = {a.id: a.external_id for a in await self.repo.artifacts(run.dataset_id)}
        links = await self.repo.rows(TlrLink, run_id)
        predicted = {
            (artifacts[link.source_artifact_id], artifacts[link.target_artifact_id])
            for link in links
        }
        elements = {e.id: e for e in await self.repo.rows(TlrElement, run_id)}
        candidates = await self.repo.rows(TlrCandidate, run_id)
        retrieved = {
            (elements[c.source_element_id].external_id, elements[c.target_element_id].external_id)
            for c in candidates
        }
        tp, fp, fn = len(predicted & gold), len(predicted - gold), len(gold - predicted)
        metrics = {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": tp / len(predicted) if predicted else 0.0,
            "recall": tp / len(gold) if gold else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0,
            "candidate_recall": len(retrieved & gold) / len(gold) if gold else 0.0,
            "gold_count": len(gold),
            "predicted_count": len(predicted),
            "unretrieved_gold_count": len(gold - retrieved),
            "zero_denominator_policy": "zero",
            "scope": "selected source/target sets",
            "run_outcome": (run.manifest or {}).get("outcome", "completed"),
            "node_failure_count": len((run.manifest or {}).get("node_failures", [])),
        }
        evaluation = TlrEvaluation(
            run_id=run_id,
            metrics=metrics,
            gold_links=[list(pair) for pair in sorted(gold)],
            provenance=request.provenance,
        )
        self.session.add(evaluation)
        await self.session.commit()
        await self.session.refresh(evaluation)
        return evaluation
