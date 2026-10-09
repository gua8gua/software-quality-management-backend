from dataclasses import dataclass, field

import bm25s
import jieba
import numpy as np
from scipy.spatial.distance import cdist
from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.traceability.models import RetrievalProjection, TraceUnit


@dataclass
class HybridCandidate:
    source: TraceUnit
    target: TraceUnit
    dense_rank: int | None = None
    dense_score: float | None = None
    synopsis_dense_rank: int | None = None
    synopsis_dense_score: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    rrf_score: float = 0.0
    fused_rank: int | None = None
    retrieval_sources: set[str] = field(default_factory=set)
    structural_reasons: list[dict] = field(default_factory=list)
    reranker_rank: int | None = None
    reranker_score: float | None = None


class ChineseTokenizer:
    """Search-mode segmentation supplied by the maintained jieba-py package."""

    name = "jieba-py"

    def __init__(self, user_terms: list[str] | None = None):
        self._tokenizer = jieba.Tokenizer()
        for term in user_terms or []:
            self._tokenizer.add_word(term)

    def tokenize(self, text: str) -> list[str]:
        return [
            token.strip().lower() for token in self._tokenizer.cut_for_search(text) if token.strip()
        ]


class DenseRetriever:
    """Exact cosine retrieval: pgvector on PostgreSQL, SciPy for local SQLite diagnostics."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def retrieve(
        self,
        sources: list[TraceUnit],
        targets: list[TraceUnit],
        projections: dict[str, RetrievalProjection],
        *,
        channel: str,
        top_k: int,
    ) -> dict[tuple[str, str], tuple[int, float]]:
        source_projection_id = (
            (lambda unit: unit.raw_projection_id)
            if channel == "raw"
            else (lambda unit: unit.synopsis_projection_id)
        )
        target_by_projection = {
            source_projection_id(unit): unit
            for unit in targets
            if source_projection_id(unit) is not None
        }
        if not target_by_projection:
            return {}
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            return await self._postgres(
                sources, source_projection_id, target_by_projection, projections, top_k
            )
        return self._local(sources, source_projection_id, target_by_projection, projections, top_k)

    async def _postgres(
        self,
        sources,
        projection_id,
        target_by_projection,
        projections,
        top_k,
    ):
        rows: dict[tuple[str, str], tuple[int, float]] = {}
        target_ids = list(target_by_projection)
        for source in sources:
            source_id = projection_id(source)
            query = projections[source_id].embedding if source_id else None
            if query is None:
                continue
            distance = RetrievalProjection.embedding.cosine_distance(query)
            statement = (
                select(RetrievalProjection.id, (literal(1.0) - distance).label("score"))
                .where(
                    RetrievalProjection.id.in_(target_ids),
                    RetrievalProjection.embedding.is_not(None),
                )
                .order_by(distance)
                .limit(min(top_k, len(target_ids)))
            )
            for rank, (target_projection_id, score) in enumerate(
                (await self.session.execute(statement)).all(), start=1
            ):
                target = target_by_projection[target_projection_id]
                rows[(source.id, target.id)] = (rank, float(score))
        return rows

    def _local(
        self,
        sources,
        projection_id,
        target_by_projection,
        projections,
        top_k,
    ):
        usable = [
            (projection_id, unit)
            for projection_id, unit in target_by_projection.items()
            if projections[projection_id].embedding is not None
        ]
        if not usable:
            return {}
        matrix = np.asarray([projections[item[0]].embedding for item in usable], dtype=np.float32)
        rows: dict[tuple[str, str], tuple[int, float]] = {}
        for source in sources:
            source_id = projection_id(source)
            query = projections[source_id].embedding if source_id else None
            if query is None:
                continue
            scores = 1.0 - cdist(np.asarray([query], dtype=np.float32), matrix, metric="cosine")[0]
            order = np.argsort(-scores)[: min(top_k, len(usable))]
            for rank, index in enumerate(order.tolist(), start=1):
                target = usable[index][1]
                rows[(source.id, target.id)] = (rank, float(scores[index]))
        return rows

    async def search_vector(
        self,
        query: list[float],
        targets: list[TraceUnit],
        projections: dict[str, RetrievalProjection],
        *,
        top_k: int,
    ) -> list[tuple[TraceUnit, int, float]]:
        """Search the existing raw TLR projection index with an external query vector."""
        usable = [
            (unit, projections[unit.raw_projection_id])
            for unit in targets
            if unit.raw_projection_id in projections
            and projections[unit.raw_projection_id].embedding is not None
        ]
        if not usable:
            return []
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            distance = RetrievalProjection.embedding.cosine_distance(query)
            ids = [projection.id for _, projection in usable]
            units = {projection.id: unit for unit, projection in usable}
            statement = (
                select(RetrievalProjection.id, (literal(1.0) - distance).label("score"))
                .where(RetrievalProjection.id.in_(ids), RetrievalProjection.embedding.is_not(None))
                .order_by(distance)
                .limit(min(top_k, len(ids)))
            )
            return [
                (units[projection_id], rank, float(score))
                for rank, (projection_id, score) in enumerate(
                    (await self.session.execute(statement)).all(), start=1
                )
            ]
        matrix = np.asarray([row.embedding for _, row in usable], dtype=np.float32)
        scores = 1.0 - cdist(np.asarray([query], dtype=np.float32), matrix, metric="cosine")[0]
        order = np.argsort(-scores)[: min(top_k, len(usable))]
        return [
            (usable[index][0], rank, float(scores[index]))
            for rank, index in enumerate(order.tolist(), start=1)
        ]


class BM25Retriever:
    name = "bm25s"

    def __init__(self, tokenizer: ChineseTokenizer):
        self.tokenizer = tokenizer

    def retrieve(
        self,
        sources: list[TraceUnit],
        targets: list[TraceUnit],
        projections: dict[str, RetrievalProjection],
        *,
        top_k: int,
    ) -> dict[tuple[str, str], tuple[int, float]]:
        if not sources or not targets:
            return {}
        corpus = [
            self.tokenizer.tokenize(projections[unit.raw_projection_id].text) for unit in targets
        ]
        model = bm25s.BM25(method="lucene")
        model.index(corpus, show_progress=False)
        queries = [
            self.tokenizer.tokenize(projections[unit.raw_projection_id].text) for unit in sources
        ]
        result = model.retrieve(queries, k=min(top_k, len(targets)), show_progress=False)
        rows: dict[tuple[str, str], tuple[int, float]] = {}
        for source_index, source in enumerate(sources):
            for rank, (target_index, score) in enumerate(
                zip(result.documents[source_index], result.scores[source_index], strict=True),
                start=1,
            ):
                if float(score) <= 0:
                    continue
                rows[(source.id, targets[int(target_index)].id)] = (rank, float(score))
        return rows

    def search_text(
        self,
        query: str,
        targets: list[TraceUnit],
        projections: dict[str, RetrievalProjection],
        *,
        top_k: int,
    ) -> list[tuple[TraceUnit, int, float]]:
        """Search existing raw TLR projections without creating an Agent-only index."""
        if not query.strip() or not targets:
            return []
        corpus = [
            self.tokenizer.tokenize(projections[unit.raw_projection_id].text) for unit in targets
        ]
        model = bm25s.BM25(method="lucene")
        model.index(corpus, show_progress=False)
        result = model.retrieve(
            [self.tokenizer.tokenize(query)], k=min(top_k, len(targets)), show_progress=False
        )
        rows: list[tuple[TraceUnit, int, float]] = []
        for rank, (target_index, score) in enumerate(
            zip(result.documents[0], result.scores[0], strict=True), start=1
        ):
            if float(score) > 0:
                rows.append((targets[int(target_index)], rank, float(score)))
        return rows


def union_candidates(
    sources: list[TraceUnit],
    targets: list[TraceUnit],
    dense: dict[tuple[str, str], tuple[int, float]],
    bm25: dict[tuple[str, str], tuple[int, float]],
    *,
    rrf_k: int = 60,
) -> list[HybridCandidate]:
    """Fuse independent ranked lists with standard unweighted RRF.

    Raw retriever scores are intentionally retained for diagnostics only.  RRF
    uses each channel's 1-based rank and gives every enabled channel equal
    weight: ``sum(1 / (k + rank_i))``.
    """
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    source_map = {item.id: item for item in sources}
    target_map = {item.id: item for item in targets}
    rows: dict[tuple[str, str], HybridCandidate] = {}
    for channel, hits in (
        ("dense", dense),
        ("bm25", bm25),
    ):
        for key, (rank, score) in hits.items():
            item = rows.setdefault(
                key, HybridCandidate(source=source_map[key[0]], target=target_map[key[1]])
            )
            item.retrieval_sources.add(channel)
            setattr(item, f"{channel}_rank", rank)
            setattr(item, f"{channel}_score", score)
    for item in rows.values():
        item.rrf_score = sum(
            1.0 / (rrf_k + rank) for rank in (item.dense_rank, item.bm25_rank) if rank is not None
        )

    grouped: dict[str, list[HybridCandidate]] = {}
    for item in rows.values():
        grouped.setdefault(item.source.id, []).append(item)
    ordered: list[HybridCandidate] = []
    for source in sources:
        group = sorted(
            grouped.get(source.id, []),
            key=lambda item: (-item.rrf_score, item.target.ordinal, item.target.id),
        )
        for rank, item in enumerate(group, start=1):
            item.fused_rank = rank
        ordered.extend(group)
    return ordered
