import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.tlr.diagnostics import PreprocessingFailure, failure_detail, preflight
from app.modules.tlr.models import (
    TlrArtifact,
    TlrCandidate,
    TlrDataset,
    TlrElement,
    TlrEvaluation,
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
        for artifact in request.artifacts:
            self.session.add(
                TlrArtifact(
                    dataset_id=dataset.id, **artifact.model_dump(), sha256=digest(artifact.content)
                )
            )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        await self.session.refresh(dataset)
        return dataset

    async def create_run(self, request: RunInput, *, commit: bool = True):
        dataset = await self.repo.dataset(request.dataset_id, request.tenant_id, request.project_id)
        artifacts = await self.repo.artifacts(dataset.id)
        available = {a.external_id for a in artifacts}
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
            preflight(self.embedding, self.classifier.llm)
            async with asyncio.timeout(self.settings.tlr_run_timeout_seconds):
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

    async def _pipeline(self, run: TlrRun):
        options = RunOptions.model_validate(run.config["options"])
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
            "embedding_model": self.settings.embedding_model,
            "embedding_base_url": self.settings.model_base_url,
            "llm_model": self.classifier.llm.model,
            "llm_base_url": self.settings.llm_base_url,
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
                            artifact, role, run.id, options, self.classifier.llm
                        )
                    except (ValueError, SyntaxError) as exc:
                        raise PreprocessingFailure(str(exc)) from exc
                    destination.extend(units)
                    self.session.add_all(units)
                    await self.session.commit()
                    if len(sources) + len(targets) > self.settings.tlr_max_elements:
                        raise ValueError("TLR element limit exceeded")
        if len(sources) * min(options.top_k, len(targets)) > self.settings.tlr_max_candidates:
            raise ValueError("TLR candidate limit exceeded")
        if len(sources) * len(targets) > self.settings.tlr_max_comparisons:
            raise ValueError("TLR retrieval comparison limit exceeded")
        self.session.add_all(sources + targets)
        run.counts = {"source_elements": len(sources), "target_elements": len(targets)}
        run.stage = "embedding"
        await self.session.commit()
        elements = sources + targets
        dimension = None
        for start in range(0, len(elements), 32):
            batch = elements[start : start + 32]
            vectors = await self.embedding.embed([e.content for e in batch])
            dimension = validate_vectors(vectors, len(batch), dimension)
            for element, vector in zip(batch, vectors, strict=True):
                element.embedding = vector
            await self.session.commit()
        run.manifest = {**run.manifest, "embedding_dimension": dimension}
        run.stage = "retrieval"
        if isinstance(retriever, LissaRetriever):
            run.manifest = {**run.manifest, "runtime_artifacts": await retriever.fingerprint()}
        await self.session.commit()
        rows = await retriever.retrieve(sources, targets, options.top_k)
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
        for index, candidate in enumerate(candidates, 1):
            try:
                decision = await self.classifier.classify(
                    by_id[candidate.source_element_id], by_id[candidate.target_element_id]
                )
            except ClassificationOutputError as exc:
                candidate.evidence = {
                    "validation_status": "invalid",
                    "raw_response": exc.raw_response,
                }
                await self.session.commit()
                raise
            candidate.decision = "related" if decision.related else "unrelated"
            candidate.evidence = decision.model_dump()
            run.counts = {**run.counts, "classified": index}
            await self.session.commit()
        run.stage = "aggregation"
        await self.session.commit()
        grouped = defaultdict(list)
        for c in candidates:
            if c.decision == "related":
                pair = (
                    by_id[c.source_element_id].artifact_id,
                    by_id[c.target_element_id].artifact_id,
                )
                grouped[pair].append(c.id)
        for (source, target), evidence in grouped.items():
            self.session.add(
                TlrLink(
                    run_id=run.id,
                    source_artifact_id=source,
                    target_artifact_id=target,
                    evidence_candidate_ids=evidence,
                )
            )
        run.counts = {**run.counts, "links": len(grouped)}
        run.status, run.stage, run.finished_at = "completed", "completed", datetime.now(UTC)
        await self.session.commit()

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
