"""Artifact scope, worker protocol and recovery; no model or external service."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.db.base import Base
from app.modules.artifacts.models import Artifact, ContentNode, Project
from app.modules.information_access.batch_service import ReviewBatchService
from app.modules.information_access.passage_service import PassageService
from app.modules.information_access.review_models import (
    ReviewBatch,
    ReviewEvent,
    ReviewTask,
)
from app.modules.information_access.review_tools import ReviewTools
from app.modules.traceability.models import (
    Candidate,
    RetrievalProjection,
    SemanticAnnotation,
    TlrRun,
    TraceUnit,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(Project(id="project", tenant_id="local", name="scope"))
        db.add_all([Artifact(id=i, project_id="project", external_id=i, modality="text", engineering_role=r)
                    for i, r in [("srs", "requirement"), ("extra", "requirement"), ("sds", "design"), ("other", "design")]])
        db.add(TlrRun(id="run", tenant_id="local", project_id="project", status="completed",
                      config={"source_artifact_ids": ["srs"], "target_artifact_ids": ["sds", "other"]}))
        for i, artifact, side in [("r1", "srs", "source"), ("r2", "srs", "source"), ("r3", "srs", "source"),
                                  ("d1", "sds", "target"), ("d2", "other", "target")]:
            db.add(ContentNode(id=i, artifact_id=artifact, node_type="paragraph", original_type="text", text=i, reading_order=int(i[1:])))
            db.add(SemanticAnnotation(id="a"+i, run_id="run", node_id=i, document_type="SRS" if side == "source" else "SDS",
                                      role="primary", provider="fixture", model="fixture", model_version="1", prompt_version="1", generation_version="1"))
            db.add(RetrievalProjection(id="p"+i, run_id="run", node_id=i, channel="raw", document_type="SRS" if side == "source" else "SDS", text=i))
            db.add(TraceUnit(id="u"+i, run_id="run", artifact_id=artifact, node_id=i, side=side,
                             annotation_id="a"+i, raw_projection_id="p"+i, semantic_role="primary"))
        db.add(ContentNode(id="d3", artifact_id="sds", node_type="paragraph", original_type="text", text="d3", reading_order=3))
        db.add_all([Candidate(id="c1", run_id="run", source_unit_id="ur1", target_unit_id="ud1"),
                    Candidate(id="c2", run_id="run", source_unit_id="ur2", target_unit_id="ud1"),
                    Candidate(id="c3", run_id="run", source_unit_id="ur3", target_unit_id="ud2")])
        await db.commit()
        yield db
    await engine.dispose()


async def setup_batch(session):
    created = await ReviewTools(session, "project").create_reviews("run", ["srs"], ["sds"])
    service = ReviewBatchService(session, "project")
    await service.claim(created["batch_id"], worker_token="a")
    return created, service


async def result(service, created, review_id, status, token="a", identity=None, **details):
    started = await service.begin_review_attempt(created["batch_id"], review_id, identity or f"{token}:{review_id}", token)
    payload = {"attempt": started["attempt"], "attempt_id": started["attempt_id"], "status": status, **details}
    await service.progress(created["batch_id"], review_id, payload, token)
    return payload


@pytest.mark.asyncio
async def test_selected_artifacts_enumerate_supported_nodes_and_keep_exclusions(session):
    created = await ReviewTools(session, "project").create_reviews("run", ["srs", "extra"], ["sds"], "scope task")
    batch = await ReviewBatchService(session, "project").detail(created["batch_id"])
    assert {r["requirement_node_id"] for r in batch["reviews"]} == {"r1", "r2"}
    assert batch["scope"]["excluded_artifact_ids"] == ["extra"] and batch["scope"]["unsupported_source_node_count"] == 1
    for review_id in created["review_ids"]:
        with pytest.raises(ValueError, match="outside"):
            await PassageService(session, "project")._target_text(review_id, "d2")


@pytest.mark.asyncio
async def test_selected_tlr_nodes_are_persisted_as_the_analysis_scope(session):
    created = await ReviewTools(session, "project").create_reviews(
        "run", ["srs"], ["sds"], "node scope", "", ["r1"], ["d1"])
    batch = await ReviewBatchService(session, "project").detail(created["batch_id"])
    assert {r["requirement_node_id"] for r in batch["reviews"]} == {"r1"}
    assert batch["scope"]["selected_source_node_ids"] == ["r1"]
    assert batch["scope"]["selected_target_node_ids"] == ["d1"]
    with pytest.raises(ValueError, match="selected TLR node scope"):
        await PassageService(session, "project")._target_text(
            created["review_ids"][0], "d3")


@pytest.mark.asyncio
async def test_failed_independent_review_does_not_prevent_the_next(session):
    created, service = await setup_batch(session)
    first, second = created["review_ids"]
    await result(service, created, first, "blocked", error_code="quote_invalid", error_message="not exact")
    await result(service, created, second, "completed")
    assert (await service.finish(created["batch_id"], "a"))["status"] == "partial"
    assert (await service.detail(created["batch_id"]))["finished_count"] == 2
    await service.claim(created["batch_id"], resume=True, worker_token="b")
    for review_id in created["review_ids"]:
        await result(service, created, review_id, "completed", "b")
    assert (await service.finish(created["batch_id"], "b"))["status"] == "completed"
    assert any(e.payload_json.get("error_code") == "quote_invalid" for e in await session.scalars(select(ReviewEvent)))


@pytest.mark.asyncio
async def test_wrong_scope_or_missing_execution_identity_is_rejected(session):
    created, service = await setup_batch(session)
    other = await ReviewTools(session, "project").create_reviews("run", ["srs"], ["sds"])
    with pytest.raises(ValueError, match="outside"):
        await service.progress(created["batch_id"], other["review_ids"][0], {"status": "completed"}, "a")
    await service.begin_review_attempt(created["batch_id"], created["review_ids"][0], "attempt1", "a")
    for data in [{"status": "completed"}, {"status": "completed", "attempt": 1, "attempt_id": "foreign"}]:
        with pytest.raises(ValueError, match="current review attempt"):
            await service.progress(created["batch_id"], created["review_ids"][0], data, "a")


@pytest.mark.asyncio
async def test_result_replay_is_idempotent_after_sentence_errors_are_normalized(session):
    created, service = await setup_batch(session)
    review_id = created["review_ids"][0]
    payload = await result(service, created, review_id, "partial", results=[{
        "sentence_id": "s", "execution_status": "blocked", "error_code": "ModelBehaviorError", "error_message": "invalid"}])
    before = list(await session.scalars(select(ReviewEvent)))
    await service.progress(created["batch_id"], review_id, payload, "a")
    assert len(list(await session.scalars(select(ReviewEvent)))) == len(before)
    assert (await service.detail(created["batch_id"]))["reviews"][0]["execution"]["sentence_errors"][0]["error_code"] == "ModelBehaviorError"
    with pytest.raises(ValueError, match="different terminal"):
        await service.progress(created["batch_id"], review_id, {**payload, "status": "completed"}, "a")


@pytest.mark.asyncio
async def test_attempt_start_is_durable_and_idempotent(session):
    created, service = await setup_batch(session)
    review_id = created["review_ids"][0]
    await result(service, created, review_id, "blocked", error_code="old_timeout")
    await service.interrupt(created["batch_id"], "a")
    await service.claim(created["batch_id"], resume=True, worker_token="b")
    current = await service.begin_review_attempt(created["batch_id"], review_id, "fresh", "b")
    assert current["status"] == "running" and current["error_code"] is None
    assert await service.begin_review_attempt(created["batch_id"], review_id, "fresh", "b") == current
    with pytest.raises(ValueError, match="already running"):
        await service.begin_review_attempt(created["batch_id"], review_id, "different", "b")
    task = await session.scalar(select(ReviewTask).where(ReviewTask.review_id == review_id, ReviewTask.task_key == "batch_execution"))
    assert task.status == "running" and task.error_code is None


@pytest.mark.asyncio
async def test_resumed_batch_cannot_finish_using_previous_failed_results(session):
    created, service = await setup_batch(session)
    for review_id in created["review_ids"]:
        await result(service, created, review_id, "blocked")
    await service.finish(created["batch_id"], "a")
    await service.claim(created["batch_id"], resume=True, worker_token="b")
    await result(service, created, created["review_ids"][0], "completed", "b")
    with pytest.raises(ValueError, match="not processed every"):
        await service.finish(created["batch_id"], "b")
    await result(service, created, created["review_ids"][1], "blocked", "b")
    assert (await service.finish(created["batch_id"], "b"))["status"] == "partial"


@pytest.mark.asyncio
async def test_continuation_plan_reuses_terminal_failure_without_inventing_success(session):
    created, service = await setup_batch(session)
    first, second = created["review_ids"]
    await result(service, created, first, "blocked", error_code="invalid_model_output")
    await service.interrupt(created["batch_id"], "a")
    await service.claim(created["batch_id"], resume=True, worker_token="b", execution_review_ids=[second])
    with pytest.raises(ValueError, match="execution plan"):
        await service.begin_review_attempt(created["batch_id"], first, "forbidden", "b")
    with pytest.raises(ValueError, match="unfinished"):
        await service.finish(created["batch_id"], "b")
    await result(service, created, second, "completed", "b")
    finished = await service.finish(created["batch_id"], "b")
    assert finished["status"] == "partial"
    assert finished["status_counts"]["blocked"] == 1
    assert finished["execution_plan"]["review_ids"] == [second]


@pytest.mark.asyncio
async def test_lease_renewal_and_expiry_fence_every_business_write(session):
    created, service = await setup_batch(session)
    review_id = created["review_ids"][0]
    attempt = await service.begin_review_attempt(created["batch_id"], review_id, "old", "a")
    await service.assert_review_writer(review_id, "a", attempt["attempt_id"])
    with pytest.raises(ValueError, match="live worker"):
        await service.claim(created["batch_id"], resume=True, worker_token="b")
    row = await session.get(ReviewBatch, created["batch_id"])
    deadline = row.lease_expires_at
    await service.heartbeat(created["batch_id"], "a")
    assert row.lease_expires_at.replace(tzinfo=UTC) >= deadline.replace(tzinfo=UTC)
    row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(ValueError, match="not active"):
        await service.assert_review_writer(review_id, "a", "old")
    await service.claim(created["batch_id"], resume=True, worker_token="b")
    await service.begin_review_attempt(created["batch_id"], review_id, "new", "b")
    for operation in [service.assert_review_writer(review_id, "a", "old"),
                      service.progress(created["batch_id"], review_id, {**attempt, "status": "completed"}, "a"),
                      service.interrupt(created["batch_id"], "a"), service.finish(created["batch_id"], "a")]:
        with pytest.raises(ValueError, match="another worker"):
            await operation
    with pytest.raises(ValueError, match="current running attempt"):
        await service.assert_review_writer(review_id, "b", "old")
    assert "worker_token" not in await service.detail(created["batch_id"])


@pytest.mark.asyncio
async def test_terminal_batch_stays_fenced(session):
    created, service = await setup_batch(session)
    for review_id in created["review_ids"]:
        await result(service, created, review_id, "completed")
    await service.finish(created["batch_id"], "a")
    assert (await service.finish(created["batch_id"], "a"))["status"] == "completed"
    with pytest.raises(ValueError, match="not active"):
        await service.assert_review_writer(created["review_ids"][0], "a", "a:x")


@pytest.mark.asyncio
async def test_interruption_closes_active_attempt_but_preserves_completed_results(session):
    created, service = await setup_batch(session)
    first, second = created["review_ids"]
    await result(service, created, first, "completed")
    started = await service.begin_review_attempt(created["batch_id"], second, "interrupted", "a")
    row = await session.get(ReviewBatch, created["batch_id"])
    row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    # The same worker may close its expired lease, but never regain write access.
    await service.interrupt(created["batch_id"], "a", "ConnectError", "backend transport lost")
    batch = await service.detail(created["batch_id"])
    executions = {r["review_id"]: r["execution"] for r in batch["reviews"]}
    assert batch["status"] == "interrupted" and batch["status_counts"]["running"] == 0
    assert executions[first]["status"] == "completed"
    assert executions[second]["attempt_id"] == started["attempt_id"]
    assert executions[second]["error_code"] == "ConnectError"
    task = await session.scalar(select(ReviewTask).where(
        ReviewTask.review_id == second, ReviewTask.task_key == "batch_execution"))
    assert task.status == "blocked" and task.error_message == "backend transport lost"
    events_before = len(list(await session.scalars(select(ReviewEvent))))
    await service.interrupt(created["batch_id"], "a")
    assert len(list(await session.scalars(select(ReviewEvent)))) == events_before
    with pytest.raises(ValueError, match="not active"):
        await service.assert_review_writer(second, "a", "interrupted")


@pytest.mark.asyncio
async def test_lock_refreshes_token_instead_of_trusting_cached_identity_map(session):
    created, service = await setup_batch(session)
    cached = await session.get(ReviewBatch, created["batch_id"])
    assert cached.worker_token == "a"
    async with async_sessionmaker(session.bind, expire_on_commit=False)() as other:
        changed = await other.get(ReviewBatch, created["batch_id"])
        changed.worker_token = "b"
        await other.commit()
    assert cached.worker_token == "a"  # the old session really has stale data
    with pytest.raises(ValueError, match="another worker"):
        await service.heartbeat(created["batch_id"], "a")
    assert cached.worker_token == "b"


@pytest.mark.asyncio
async def test_single_workspace_uses_same_executor_without_copying_facts(session):
    created, service = await setup_batch(session)
    assert (await service.execution_batch(created["review_ids"][0]))["batch_id"] == created["batch_id"]
    standalone = await ReviewTools(session, "project").create_review("run", "r1")
    attached = await service.execution_batch(standalone["review_id"])
    assert await service.execution_batch(standalone["review_id"]) == attached
    assert (await service.detail(attached["batch_id"]))["review_ids"] == [standalone["review_id"]]
