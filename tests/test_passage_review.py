"""Deterministic review regression tests; no models, TLR or external services."""

from pathlib import Path
from runpy import run_path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 -- register all referenced tables
from app.db.base import Base
from app.modules.artifacts.models import Artifact, ContentNode, Project
from app.modules.information_access.audit_service import AuditService
from app.modules.information_access.passage_service import PassageService, covers_text
from app.modules.information_access.review_models import (
    Review,
    ReviewEvent,
    ReviewEvidence,
    ReviewEvidenceCheck,
    ReviewPassage,
    ReviewPassageAssessment,
    ReviewSentence,
    ReviewTask,
)
from app.modules.traceability.models import TlrRun


class FixturePassages(PassageService):
    async def _mandatory_ids(self, review):
        return {"design1", "design2"}


@pytest.mark.asyncio
async def test_resume_retains_partial_assessments_and_error_history(service):
    passage = await opened(service)
    await service.assess("review", passage["passage_id"], [decision(service.check_ids[0])], finalize=False)
    await service.passage_error("review", passage["passage_id"], "worker_interrupted", "process stopped")
    result = await service.resume_blocked("review")
    assert result["resumed_passage_ids"] == [passage["passage_id"]]
    row = await service.session.get(ReviewPassage, passage["passage_id"])
    assert row.status == "pending" and row.error_code is None
    assert await service.session.scalar(select(func.count()).select_from(ReviewPassageAssessment)) == 1
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 1
    result = await service.assess("review", passage["passage_id"], [decision(service.check_ids[1])])
    assert result["can_stop_for_sufficient_evidence"]


@pytest.mark.asyncio
async def test_resume_does_not_clear_errors_after_target_text_changes(service):
    passage = await opened(service)
    await service.passage_error("review", passage["passage_id"], "worker_interrupted", "process stopped")
    node = await service.session.get(ContentNode, "design1")
    node.text += " changed"
    await service.session.commit()
    result = await service.resume_blocked("review")
    assert not result["resumed_passage_ids"]
    row = await service.session.get(ReviewPassage, passage["passage_id"])
    assert row.status == "blocked" and row.error_code == "worker_interrupted"


@pytest_asyncio.fixture
async def service():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Project(id="project", tenant_id="local", name="test"))
        session.add_all([Artifact(id="srs", project_id="project", external_id="SRS", engineering_role="requirement"),
                         Artifact(id="sds", project_id="project", external_id="SDS", engineering_role="design")])
        session.add_all([
            ContentNode(id="requirement", artifact_id="srs", node_type="paragraph", original_type="text",
                        reading_order=0, text="功能A；约束B"),
            ContentNode(id="design1", artifact_id="sds", node_type="paragraph", original_type="text",
                        reading_order=0, text="实现A，并满足B。"),
            ContentNode(id="design2", artifact_id="sds", node_type="paragraph", original_type="text",
                        reading_order=1, text="其它设计。"),
        ])
        session.add(TlrRun(id="run", tenant_id="local", project_id="project", status="completed",
                           config={"source_artifact_ids": ["srs"], "target_artifact_ids": ["sds"]}))
        session.add(Review(id="review", project_id="project", tlr_run_id="run",
                           requirement_node_id="requirement", status="prepared"))
        session.add(ReviewSentence(id="sentence", review_id="review", source_node_id="requirement",
                                   ordinal=1, start_offset=0, end_offset=7, text="功能A；约束B"))
        await session.commit()
        instance = FixturePassages(session, "project")
        c1 = await instance.add_check("review", "sentence", "实现A", "function_result", 0, 3)
        c2 = await instance.add_check("review", "sentence", "满足B", "condition_constraint", 4, 7)
        instance.check_ids = [c1["check_id"], c2["check_id"]]
        yield instance
    await engine.dispose()


async def opened(service, node="design1"):
    await service.finalize_checks("review", "sentence", service.check_ids)
    length = 9 if node == "design1" else 5
    return await service.open_passage("review", "sentence", node, 0, length)


def decision(check_id, *, relation="supporting", status="supported", quote="实现A，并满足B。"):
    return {"check_id": check_id, "relation": relation, "status": status,
            "note": "结合完整源语段及已保存证据作出当前判断",
            "quotes": [{"start_offset": 0, "end_offset": len(quote), "exact_quote": quote}]
            if relation in {"supporting", "counter", "context"} else []}


@pytest.mark.asyncio
async def test_partial_extraction_cannot_start_and_finalized_checks_cannot_expand(service):
    with pytest.raises(ValueError, match="finalize"):
        await service.open_passage("review", "sentence", "design1", 0, 9)
    await service.finalize_checks("review", "sentence", service.check_ids)
    with pytest.raises(ValueError, match="finalized"):
        await service.add_check("review", "sentence", "新增要求", "function_result", 0, 3)


@pytest.mark.asyncio
async def test_missing_check_rejects_entire_transaction(service):
    passage = await opened(service)
    with pytest.raises(ValueError, match="every check"):
        await service.assess("review", passage["passage_id"], [decision(service.check_ids[0])])
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 0
    assert await service.session.scalar(select(func.count()).select_from(ReviewPassageAssessment)) == 0
    assert {c.status for c in await service._checks("sentence")} == {"pending"}


@pytest.mark.asyncio
async def test_one_quote_supports_multiple_checks_and_sufficient_evidence_stops(service):
    passage = await opened(service)
    first = await service.assess("review", passage["passage_id"],
                                 [decision(service.check_ids[0])], finalize=False)
    assert first["passage_status"] == "pending"
    assert first["remaining_check_ids"] == [service.check_ids[1]]
    result = await service.assess("review", passage["passage_id"], [decision(service.check_ids[1])])
    assert result["can_stop_for_sufficient_evidence"]
    assert result["mandatory_remaining_node_ids"] == ["design2"]
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 1
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidenceCheck)) == 2
    # Unread/unfinished material is not renamed completed just to allow termination.
    extra = await service.open_passage("review", "sentence", "design2", 0, 5)
    await service.passage_error("review", extra["passage_id"], "timeout", "真实执行错误")
    completed = await service.conclude("review", "sentence", [
        {"check_id": c, "status": "supported", "note": "综合全部逐字证据，分别实现A并满足B。"}
        for c in service.check_ids])
    assert completed["completed"]
    assert completed["investigation_scope"]["termination_reason"] == "all_checks_supported"
    assert not completed["investigation_scope"]["exhaustive"]
    assert (await service.session.get(ReviewPassage, extra["passage_id"])).status == "blocked"
    workspace = await AuditService(service.session, "project").workspace("review")
    assert workspace["status"] == "completed"
    assert workspace["summary"]["completed_workflow"]
    assert workspace["summary"]["semantic_result"] == "supported"
    assert len({e["evidence_id"] for e in workspace["evidence"]}) == 1
    assert {e["check_id"] for e in workspace["evidence"]} == set(service.check_ids)


@pytest.mark.asyncio
async def test_irrelevant_material_does_not_become_omission_evidence(service):
    passage = await opened(service)
    result = await service.assess("review", passage["passage_id"],
                                 [decision(c, relation="irrelevant", status="pending") for c in service.check_ids])
    assert not result["can_stop_for_sufficient_evidence"]
    assert {c.status for c in await service._checks("sentence")} == {"pending"}
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 0
    with pytest.raises(ValueError, match="unassessed"):
        await service.conclude("review", "sentence", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["irrelevant", "needs_context"])
async def test_unused_malformed_quotes_are_ignored_without_creating_evidence(service, relation):
    from app.modules.information_access.review_router import PassageDecision

    passage = await opened(service)
    decisions = [decision(c, relation=relation, status="pending") for c in service.check_ids]
    for item in decisions:
        item["quotes"] = [{"not_a_quote": "unused"}, {"start_offset": -500}]
        assert PassageDecision.model_validate(item).quotes == []
    result = await service.assess("review", passage["passage_id"], decisions)
    assert result["passage_status"] == "completed"  # processing, not satisfaction
    assert not result["can_stop_for_sufficient_evidence"]
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 0


@pytest.mark.asyncio
async def test_one_local_retry_is_durable_idempotent_and_then_skipped(service):
    passage = await opened(service)
    key = passage["passage_id"]
    first = await service.passage_error("review", key, "quote_invalid", "原文引文不匹配",
                                        retryable=True, failure_id="call-1")
    assert first["execution_status"] == "retry_required" and first["retry_allowed"]
    replay = await service.passage_error("review", key, "quote_invalid", "原文引文不匹配",
                                         retryable=True, failure_id="call-1")
    assert replay["replayed"] and replay["retry_allowed"]
    assert (await service.session.get(ReviewPassage, key)).status == "pending"
    second = await service.passage_error("review", key, "quote_invalid", "重试仍然不匹配",
                                         retryable=True, failure_id="call-2")
    assert second["execution_status"] == "blocked" and second["skipped"]
    assert len(await service._passage_failures("review", key)) == 2
    recovery = await service.resume_blocked("review")
    assert not recovery["resumed_passage_ids"]
    with pytest.raises(ValueError, match="blocked"):
        await service.assess("review", key, [decision(c) for c in service.check_ids])
    scope = await service.coverage("review", "sentence")
    assert not scope["unfinished_passage_ids"]
    assert scope["blocked_passage_ids"] == [key]
    assert scope["mandatory_unattempted_node_ids"] == ["design2"]
    other = await service.open_passage("review", "sentence", "design2", 0, 5)
    await service.assess("review", other["passage_id"], [
        decision(c, relation="irrelevant", status="pending") for c in service.check_ids])
    assert (await service.session.get(ReviewPassage, other["passage_id"])).status == "completed"
    with pytest.raises(ValueError, match="unassessed"):
        await service.ensure_complete("review", "sentence")


@pytest.mark.asyncio
async def test_corrected_retry_completes_without_erasing_failure_event(service):
    passage = await opened(service)
    key = passage["passage_id"]
    await service.passage_error("review", key, "quote_invalid", "原文引文不匹配", retryable=True)
    result = await service.assess("review", key, [decision(c) for c in service.check_ids])
    assert result["passage_status"] == "completed" and result["can_stop_for_sufficient_evidence"]
    assert (await service.session.get(ReviewPassage, key)).error_code is None
    events = await service._passage_failures("review", key)
    assert len(events) == 1 and events[0].payload_json["error_message"] == "原文引文不匹配"
    late = await service.passage_error("review", key, "lost_response", "响应丢失", retryable=True)
    assert late["execution_status"] == "completed" and late["error_recorded"]
    assert (await service.session.get(ReviewPassage, key)).status == "completed"


@pytest.mark.asyncio
async def test_all_supported_can_finish_with_exhausted_failure_preserved(service):
    failed = await opened(service, "design2")
    key = failed["passage_id"]
    await service.passage_error("review", key, "invalid_response", "首次失败", retryable=True)
    await service.passage_error("review", key, "invalid_response", "重试失败", retryable=True)
    good = await service.open_passage("review", "sentence", "design1", 0, 9)
    await service.assess("review", good["passage_id"], [decision(c) for c in service.check_ids])
    result = await service.conclude("review", "sentence", [
        {"check_id": c, "status": "supported", "note": "综合合法原文证据确认完整满足"} for c in service.check_ids])
    assert result["completed"]
    assert result["investigation_scope"]["termination_reason"] == "all_checks_supported"
    assert result["investigation_scope"]["execution_errors"][0]["error_message"] == "重试失败"
    assert not result["investigation_scope"]["exhaustive"]
    assert (await service.session.get(ReviewPassage, key)).status == "blocked"
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvent).where(
        ReviewEvent.event_type == "passage_execution_failed")) == 2


@pytest.mark.asyncio
async def test_satisfaction_can_stop_before_current_passage_all_check_assessment(service):
    await service.finalize_checks("review", "sentence", service.check_ids)
    first = await service.open_passage("review", "sentence", "design1", 0, 3)
    await service.assess("review", first["passage_id"], [
        decision(service.check_ids[0], quote="实现A"),
        decision(service.check_ids[1], relation="irrelevant", status="pending"),
    ])
    second = await service.open_passage("review", "sentence", "design1", 5, 9)
    b = decision(service.check_ids[1], quote="满足B。")
    b["quotes"][0]["start_offset"], b["quotes"][0]["end_offset"] = 5, 9
    result = await service.assess("review", second["passage_id"], [b])
    assert result["can_stop_for_sufficient_evidence"]
    assert result["passage_status"] == "pending"  # c1 was not assessed for this range
    completed = await service.conclude("review", "sentence", [
        {"check_id": c, "status": "supported", "note": "综合两个资料范围，分别实现A并满足B。"}
        for c in service.check_ids])
    assert completed["completed"]
    assert completed["investigation_scope"]["termination_reason"] == "all_checks_supported"
    assert not completed["investigation_scope"]["exhaustive"]


@pytest.mark.asyncio
async def test_explicit_conclusion_ledger_is_incremental_and_cannot_adopt_last_passage(service):
    passage = await opened(service)
    await service.assess("review", passage["passage_id"], [decision(c) for c in service.check_ids])
    with pytest.raises(ValueError, match="explicit cumulative"):
        await service.conclude("review", "sentence", [])
    for index, check_id in enumerate(service.check_ids):
        result = await service.conclude("review", "sentence", [{"check_id": check_id,
            "status": "supported", "note": f"综合所有证据后的第{index + 1}项结论"}], finalize=False)
        assert not result["completed"]
    task = await service.session.scalar(select(ReviewTask).where(ReviewTask.task_key == "analysis:sentence"))
    assert len(task.output_json["conclusions"]) == 2
    completed = await service.conclude("review", "sentence", [])
    assert completed["completed"]
    assert completed["investigation_scope"]["conclusions_complete"]
    assert not completed["investigation_scope"]["remaining_conclusion_check_ids"]


@pytest.mark.asyncio
async def test_changed_material_invalidates_partial_final_conclusions(service):
    passage = await opened(service)
    await service.assess("review", passage["passage_id"], [decision(c) for c in service.check_ids])
    await service.conclude("review", "sentence", [{"check_id": service.check_ids[0],
        "status": "supported", "note": "已综合当时全部证据"}], finalize=False)
    await service.open_passage("review", "sentence", "design2", 0, 5)
    scope = await service.coverage("review", "sentence")
    assert set(scope["remaining_conclusion_check_ids"]) == set(service.check_ids)
    with pytest.raises(ValueError, match="explicit cumulative"):
        await service.conclude("review", "sentence", [{"check_id": service.check_ids[1],
            "status": "supported", "note": "新资料后的第二项结论"}])


@pytest.mark.asyncio
async def test_cumulative_correction_can_reopen_investigation_without_erasing_evidence(service):
    passage = await opened(service)
    await service.assess("review", passage["passage_id"], [decision(c) for c in service.check_ids])
    result = await service.conclude("review", "sentence", [{"check_id": service.check_ids[0],
        "status": "uncertain", "note": "综合时发现现有证据仅实现部分条件，需要进一步调查"}], finalize=False)
    assert not result["investigation_scope"]["can_stop_for_sufficient_evidence"]
    assert (await service._check("review", service.check_ids[0])).status == "uncertain"
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 1
    extra = await service.open_passage("review", "sentence", "design2", 0, 5)
    assert extra["status"] == "pending"


@pytest.mark.asyncio
async def test_foreign_or_out_of_range_evidence_cannot_commit(service):
    passage = await opened(service)
    invalid = decision(service.check_ids[1])
    invalid["quotes"][0]["end_offset"] = 99
    with pytest.raises(ValueError, match="outside investigated passage"):
        await service.assess("review", passage["passage_id"], [decision(service.check_ids[0]), invalid])
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 0
    assert {c.status for c in await service._checks("sentence")} == {"pending"}
    with pytest.raises(ValueError, match="foreign check"):
        await service.assess("review", passage["passage_id"], [decision("foreign")])


@pytest.mark.asyncio
async def test_lost_response_replay_does_not_rewind_later_status(service):
    passage = await opened(service)
    decisions = [decision(c) for c in service.check_ids]
    await service.assess("review", passage["passage_id"], decisions)
    await service.update_check("review", service.check_ids[0], "uncertain", "后来出现待澄清事项")
    await service.assess("review", passage["passage_id"], decisions)
    assert (await service._check("review", service.check_ids[0])).status == "uncertain"
    assert await service.session.scalar(select(func.count()).select_from(ReviewEvidence)) == 1


@pytest.mark.asyncio
async def test_one_passage_failure_does_not_prevent_independent_passage(service):
    first = await opened(service)
    await service.passage_error("review", first["passage_id"], "provider_failure", "实际模型不可用")
    second = await service.open_passage("review", "sentence", "design2", 0, 5)
    await service.assess("review", second["passage_id"],
                         [decision(c, relation="irrelevant", status="pending") for c in service.check_ids])
    assert (await service.session.get(ReviewPassage, first["passage_id"])).status == "blocked"
    assert (await service.session.get(ReviewPassage, second["passage_id"])).status == "completed"
    assert {c.status for c in await service._checks("sentence")} == {"pending"}


def test_range_coverage_requires_no_gaps():
    assert covers_text([(0, 3), (2, 7)], 7)
    assert not covers_text([(0, 3), (4, 7)], 7)
    assert not covers_text([], 7)


def test_migration_preserves_existing_evidence_association():
    """Exercise migration only in memory, never the project's actual database."""
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    for name in ("requirement_reviews", "content_nodes", "trace_candidates"):
        sa.Table(name, metadata, sa.Column("id", sa.String(36), primary_key=True))
    sentences = sa.Table("review_sentences", metadata, sa.Column("id", sa.String(36), primary_key=True),
                         sa.Column("review_id", sa.String(36)))
    checks = sa.Table("review_checks", metadata, sa.Column("id", sa.String(36), primary_key=True),
                      sa.Column("sentence_id", sa.String(36)))
    evidence = sa.Table("review_evidence", metadata,
                        sa.Column("id", sa.String(36), primary_key=True),
                        sa.Column("check_id", sa.String(36), sa.ForeignKey("review_checks.id", ondelete="CASCADE")),
                        sa.Column("design_node_id", sa.String(36)),
                        sa.Column("start_offset", sa.Integer()), sa.Column("end_offset", sa.Integer()),
                        sa.Column("exact_quote", sa.Text()), sa.Column("relation", sa.String(24)),
                        sa.Column("source", sa.String(32)), sa.Column("candidate_id", sa.String(36)),
                        sa.Column("validation_status", sa.String(24)))
    sa.Index("ix_review_evidence_check_id", evidence.c.check_id)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(metadata.tables["requirement_reviews"].insert(), {"id": "review"})
        connection.execute(sentences.insert(), {"id": "sentence", "review_id": "review"})
        connection.execute(checks.insert(), {"id": "check", "sentence_id": "sentence"})
        connection.execute(evidence.insert(), {"id": "evidence", "check_id": "check",
                                               "relation": "supporting", "exact_quote": "原文"})
        migration = run_path(str(Path(__file__).parents[1] / "migrations/versions/0011_passage_review.py"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
        row = connection.execute(sa.text("SELECT id, review_id, exact_quote FROM review_evidence")).one()
        assert tuple(row) == ("evidence", "review", "原文")
        link = connection.execute(sa.text("SELECT evidence_id, check_id, relation FROM review_evidence_checks")).one()
        assert tuple(link) == ("evidence", "check", "supporting")
        assert "check_id" not in {c["name"] for c in sa.inspect(connection).get_columns("review_evidence")}
    engine.dispose()
