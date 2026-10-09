import pytest

from app.modules.traceability.models import TraceUnit
from app.modules.traceability.retrievers import union_candidates


def _unit(identifier: str, ordinal: int) -> TraceUnit:
    return TraceUnit(id=identifier, node_id=f"node-{identifier}", ordinal=ordinal)


def test_rrf_uses_standard_formula_and_k_60():
    source = _unit("source", 0)
    first, second = _unit("first", 1), _unit("second", 2)
    rows = union_candidates(
        [source],
        [first, second],
        {(source.id, first.id): (1, 999.0), (source.id, second.id): (2, -999.0)},
        {(source.id, first.id): (4, -50.0), (source.id, second.id): (2, 50.0)},
    )
    assert rows[0].target.id == second.id
    assert rows[0].rrf_score == pytest.approx(2 / 62)
    assert rows[1].rrf_score == pytest.approx(1 / 61 + 1 / 64)
    assert [item.fused_rank for item in rows] == [1, 2]


def test_rrf_rewards_two_channel_hits_instead_of_min_rank():
    source = _unit("source", 0)
    single, shared = _unit("single", 1), _unit("shared", 2)
    rows = union_candidates(
        [source],
        [single, shared],
        {(source.id, single.id): (1, 1.0), (source.id, shared.id): (2, 0.8)},
        {(source.id, shared.id): (2, 10_000.0)},
    )
    assert rows[0].target.id == shared.id
    assert rows[0].rrf_score > rows[1].rrf_score


def test_rrf_single_channel_rank_one_remains_competitive():
    source = _unit("source", 0)
    single, weak_shared = _unit("single", 1), _unit("weak-shared", 2)
    rows = union_candidates(
        [source],
        [single, weak_shared],
        {(source.id, single.id): (1, 0.1), (source.id, weak_shared.id): (100, 0.9)},
        {(source.id, weak_shared.id): (100, 99.0)},
    )
    assert rows[0].target.id == single.id
