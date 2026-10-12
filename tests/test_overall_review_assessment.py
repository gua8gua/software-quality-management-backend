"""Overall semantic conclusions are verified facts, never execution fallbacks."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 -- register referenced tables
from app.db.base import Base
from app.modules.artifacts.models import Artifact, ContentNode, Project
from app.modules.information_access.audit_service import AuditService
from app.modules.information_access.passage_service import PassageService
from app.modules.information_access.review_models import (
    Review,
    ReviewBatch,
    ReviewCheck,
    ReviewEvent,
    ReviewEvidence,
    ReviewEvidenceCheck,
    ReviewSentence,
    ReviewTask,
)
from app.modules.traceability.models import TlrRun


@pytest_asyncio.fixture
async def audit():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Project(id="project", tenant_id="local", name="test"))
        session.add_all([Artifact(id="srs", project_id="project", external_id="SRS", engineering_role="requirement"),
                         Artifact(id="sds", project_id="project", external_id="SDS", engineering_role="design")])
        session.add_all([
            ContentNode(id="r", artifact_id="srs", node_type="paragraph", original_type="text", text="功能A，约束B"),
            ContentNode(id="d", artifact_id="sds", node_type="paragraph", original_type="text", text="实现A，满足B。"),
            ContentNode(id="d2", artifact_id="sds", reading_order=1, node_type="paragraph", original_type="text", text="整体顺序与设计矛盾。"),
        ])
        session.add(TlrRun(id="run", tenant_id="local", project_id="project", status="completed",
                           config={"source_artifact_ids": ["srs"], "target_artifact_ids": ["sds"]}))
        session.add(Review(id="review", project_id="project", tlr_run_id="run", requirement_node_id="r", status="completed"))
        session.add(ReviewSentence(id="sentence", review_id="review", source_node_id="r", ordinal=1,
                                   start_offset=0, end_offset=7, text="功能A，约束B", status="completed",
                                   checks_finalized=True, checkset_version=2))
        for ordinal, check_id in enumerate(["c1", "c2"], 1):
            session.add(ReviewCheck(id=check_id, sentence_id="sentence", ordinal=ordinal,
                                    statement=check_id, status="supported", note="已有逐字证据"))
        session.add(ReviewEvidence(id="e", review_id="review", design_node_id="d", start_offset=0,
                                   end_offset=8, exact_quote="实现A，满足B。", validation_status="valid", source="fixture"))
        session.add_all([ReviewEvidenceCheck(evidence_id="e", check_id=c, relation="supporting") for c in ["c1", "c2"]])
        session.add(ReviewTask(review_id="review", task_key="analysis:sentence", role="review_analysis", status="completed"))
        await session.commit()
        instance = AuditService(session, "project")
        await finish_fixture_checks(instance)
        yield instance
    await engine.dispose()


async def accept(audit, status="implemented", issues=None):
    proposal = await audit.propose_assessment("review", "sentence", status, "完整语块评价依据已保存事实。", issues or [])
    return await audit.validate_assessment("review", "sentence", True, "独立审核确认原文、状态和证据一致。",
                                           proposal["proposal_fingerprint"])


async def finish_fixture_checks(audit):
    review = await audit.session.get(Review, "review")
    review.status = "running"
    await audit.session.commit()
    checks = list(await audit.session.scalars(select(ReviewCheck)))
    await PassageService(audit.session, "project").conclude("review", "sentence", [
        {"check_id": c.id, "status": c.status, "note": c.note} for c in checks])


def overall_issue(**overrides):
    return {"source_quote": "功能A，约束B", "reason": "两个独立实现均存在，但组合顺序发生冲突。",
            "evidence": [{"node_id": "d2", "start_offset": 0, "end_offset": 10,
                          "exact_quote": "整体顺序与设计矛盾。", "candidate_id": None}], **overrides}


@pytest.mark.asyncio
async def test_old_completed_checks_are_not_an_overall_verdict(audit):
    workspace = await audit.workspace("review")
    assert workspace["assessment"]["status"] == "not_analyzed"
    assert workspace["sentences"][0]["assessment"]["pending_reason"] == "awaiting_overall_assessment"
    assert not workspace["assessment"]["suggestion_required"]
    verified = await audit.verify("review")
    assert "overall_assessment_not_validated" in verified["problems"]
    assert not verified["completed_workflow"]


@pytest.mark.asyncio
async def test_implemented_requires_independent_validation_and_has_no_suggestions(audit):
    proposal = await audit.propose_assessment("review", "sentence", "implemented", "功能与约束均有实际支持。", [])
    assert not proposal["finalized"]
    assert proposal["proposal"]["evaluation_text"] == "功能与约束均有实际支持。"
    assert proposal["status"] == "not_analyzed"
    accepted = await audit.validate_assessment("review", "sentence", True, "确认全部逐字证据及原句约束。", proposal["proposal_fingerprint"])
    assert accepted["status"] == "implemented"
    assert accepted["finalized"] and not accepted["suggestion_required"]
    verified = await audit.verify("review")
    assert verified["valid"] and verified["completed_workflow"]


@pytest.mark.asyncio
async def test_all_supported_can_have_verified_overall_issue_with_real_evidence(audit):
    with pytest.raises(ValueError, match="overall-only"):
        await audit.propose_assessment("review", "sentence", "issues", "组合异常。", [])
    result = await accept(audit, "issues", [overall_issue()])
    assert result["status"] == "issues" and result["suggestion_required"]
    issue = result["issues"][0]
    assert issue["issue_id"] and issue["evidence"][0]["evidence_id"]
    workspace = await audit.workspace("review")
    assert {c["status"] for c in workspace["checks"]} == {"supported"}
    assert workspace["overall_evidence"][0]["exact_quote"] == "整体顺序与设计矛盾。"
    assert len(workspace["evidence"]) == 2  # the existing check associations are unchanged


@pytest.mark.asyncio
async def test_overall_quote_scope_and_offsets_are_strict(audit):
    invalid = overall_issue()
    invalid["evidence"][0]["node_id"] = "r"
    with pytest.raises(ValueError, match="outside TLR target"):
        await audit.propose_assessment("review", "sentence", "issues", "组合异常。", [invalid])
    invalid = overall_issue()
    invalid["evidence"][0]["exact_quote"] = "改写证据"
    with pytest.raises(ValueError, match="exact canonical"):
        await audit.propose_assessment("review", "sentence", "issues", "组合异常。", [invalid])
    assert list(await audit.session.scalars(select(ReviewEvidence.id))) == ["e"]


@pytest.mark.asyncio
async def test_rejection_and_stale_facts_do_not_change_checks(audit):
    proposal = await audit.propose_assessment("review", "sentence", "implemented", "满足。", [])
    rejected = await audit.validate_assessment("review", "sentence", False, "遗漏了原句组合要求。", proposal["proposal_fingerprint"])
    assert rejected["status"] == "not_analyzed" and not rejected["suggestion_required"]
    check = await audit.session.get(ReviewCheck, "c1")
    check.note = "新证据导致理由更新"
    await audit.session.commit()
    with pytest.raises(ValueError, match="facts changed|explicit cumulative"):
        await audit.validate_assessment("review", "sentence", True, "接受。", proposal["proposal_fingerprint"])
    assert {c.status for c in await audit.session.scalars(select(ReviewCheck))} == {"supported"}
    events = list(await audit.session.scalars(select(ReviewEvent.event_type)))
    assert "overall_assessment_validated" in events


@pytest.mark.asyncio
async def test_execution_error_is_separate_and_does_not_erase_valid_analysis(audit):
    await accept(audit)
    execution = ReviewTask(review_id="review", task_key="batch_execution", role="manager", status="blocked",
                           error_code="suggestion_timeout", error_message="建议阶段模型超时")
    audit.session.add(execution)
    await audit.session.commit()
    assert (await audit.workspace("review"))["assessment"]["status"] == "implemented"
    check = await audit.session.get(ReviewCheck, "c1")
    check.status = "pending"
    await audit.session.commit()
    result = (await audit.workspace("review"))["assessment"]
    assert result["status"] == "analysis_error" and not result["suggestion_required"]


@pytest.mark.asyncio
async def test_saved_batch_failure_is_visible_without_a_matching_task_row(audit):
    audit.session.add(ReviewBatch(id="batch", project_id="project", tlr_run_id="run", name="existing batch",
                                 status="interrupted", results_json={"review": {
                                     "status": "partial", "error_code": "ModelBehaviorError",
                                     "error_message": "真实保存的模型执行错误"}}))
    review = await audit.session.get(Review, "review")
    review.batch_id = "batch"
    await audit.session.commit()
    workspace = await audit.workspace("review")
    assert workspace["assessment"]["status"] == "analysis_error"
    assert "真实保存的模型执行错误" in workspace["assessment"]["evaluation_text"]
    assert not workspace["assessment"]["suggestion_required"]
    assert not list(await audit.session.scalars(select(ReviewTask).where(ReviewTask.task_key == "batch_execution")))


@pytest.mark.asyncio
async def test_current_pending_proposal_survives_saved_failure_as_a_resume_checkpoint(audit):
    audit.session.add(ReviewBatch(id="batch", project_id="project", tlr_run_id="run", name="interrupted attempt",
                                 status="interrupted", results_json={"review": {
                                     "status": "partial", "error_message": "审核Agent前中断"}}))
    review = await audit.session.get(Review, "review")
    review.batch_id = "batch"
    audit.session.add(ReviewTask(review_id="review", task_key="batch_execution", role="manager",
                                status="blocked", error_code="worker_interrupted"))
    await audit.session.commit()
    proposal = await audit.propose_assessment("review", "sentence", "implemented", "真实检查事实整体评价。", [])
    assert proposal["status"] == "analysis_error"  # historical execution failure remains visible
    assert proposal["proposal_current"] and not proposal["finalized"]
    check = await audit.session.get(ReviewCheck, "c1")
    check.note = "更新事实后旧提案不能继续审核"
    await audit.session.commit()
    sentence = (await audit.workspace("review"))["sentences"][0]
    assert not sentence["assessment"]["proposal_current"]


@pytest.mark.asyncio
async def test_global_batch_interruption_is_not_a_failure_of_unstarted_reviews(audit):
    audit.session.add(ReviewBatch(id="batch", project_id="project", tlr_run_id="run", name="interrupted batch",
                                 status="interrupted", results_json={}))
    review = await audit.session.get(Review, "review")
    review.batch_id = "batch"
    await audit.session.commit()
    result = (await audit.workspace("review"))["assessment"]
    assert result["status"] == "not_analyzed"
    assert result["sentences"][0]["pending_reason"] == "awaiting_overall_assessment"


@pytest.mark.asyncio
async def test_saved_batch_failure_does_not_override_an_accepted_current_assessment(audit):
    audit.session.add(ReviewBatch(id="batch", project_id="project", tlr_run_id="run", name="existing batch",
                                 status="partial", results_json={"review": {"status": "partial"}}))
    review = await audit.session.get(Review, "review")
    review.batch_id = "batch"
    await audit.session.commit()
    await accept(audit)
    result = (await audit.workspace("review"))["assessment"]
    assert result["status"] == "implemented" and result["finalized"]
    assert not result["suggestion_required"]


@pytest.mark.asyncio
async def test_another_sentence_evidence_does_not_invalidate_this_assessment(audit):
    await accept(audit)
    audit.session.add(ReviewSentence(id="other", review_id="review", source_node_id="r", ordinal=2,
                                    start_offset=0, end_offset=3, text="功能A"))
    audit.session.add(ReviewCheck(id="other-check", sentence_id="other", statement="独立检查点"))
    audit.session.add(ReviewEvidence(id="other-evidence", review_id="review", design_node_id="d2", start_offset=0,
                                    end_offset=10, exact_quote="整体顺序与设计矛盾。", source="fixture", validation_status="valid"))
    audit.session.add(ReviewEvidenceCheck(evidence_id="other-evidence", check_id="other-check", relation="context"))
    await audit.session.commit()
    workspace = await audit.workspace("review")
    original = next(s for s in workspace["sentences"] if s["sentence_id"] == "sentence")
    assert original["assessment"]["status"] == "implemented"
    assert original["assessment"]["finalized"]


@pytest.mark.asyncio
async def test_pending_check_cannot_be_proposed_as_semantic_uncertainty(audit):
    check = await audit.session.get(ReviewCheck, "c1")
    check.status = "pending"
    await audit.session.commit()
    with pytest.raises(ValueError, match="completed finalized"):
        await audit.propose_assessment("review", "sentence", "issues", "语义不确定。", [])


@pytest.mark.asyncio
async def test_prepared_review_cannot_propose_without_source_check_anchors(audit):
    review = await audit.session.get(Review, "review")
    review.snapshot_json = {"tlr_run_id": "run", "source_text_sha256": "fixture"}
    await audit.session.commit()
    with pytest.raises(ValueError, match="source check anchors"):
        await audit.propose_assessment("review", "sentence", "implemented", "不能安全确认。", [])


@pytest.mark.asyncio
async def test_changed_canonical_quote_invalidates_displayed_verdict(audit):
    await accept(audit)
    node = await audit.session.get(ContentNode, "d")
    node.text = "设计正文已被替换。"
    await audit.session.commit()
    result = (await audit.workspace("review"))["assessment"]
    assert result["status"] == "analysis_error"
    assert not result["finalized"] and not result["suggestion_required"]
    assert result["sentences"][0]["pending_reason"] == "canonical_content_changed"


@pytest.mark.asyncio
async def test_uncovered_keeps_investigation_scope_and_does_not_fabricate_counter_evidence(audit, monkeypatch):
    for check in await audit.session.scalars(select(ReviewCheck)):
        check.status, check.note = "insufficient", "已完成调查范围中未发现实现证据"
    await audit.session.commit()

    async def exhausted(self, review_id, sentence_id):
        return {"termination_reason": "investigated_scope_completed", "exhaustive": True,
                "checks_finalized": True, "mandatory_remaining_node_ids": [], "unfinished_passage_ids": [],
                **(await self.conclusions(await self._sentence(review_id, sentence_id), await self._checks(sentence_id)))}

    monkeypatch.setattr(PassageService, "ensure_complete", exhausted)
    await finish_fixture_checks(audit)
    accepted = await accept(audit, "uncovered")
    assert accepted["status"] == "uncovered" and accepted["suggestion_required"]
    assert accepted["proposal"]["investigation_scope"]["exhaustive"]
    assert len(list(await audit.session.scalars(select(ReviewEvidence)))) == 1
