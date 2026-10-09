"""Review business-state tools; deliberately separate from read-only information routes."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.dependencies import SessionDep
from app.modules.artifacts.models import ContentNode
from app.modules.information_access.audit_service import AuditService
from app.modules.information_access.batch_service import ReviewBatchService
from app.modules.information_access.passage_service import PassageService
from app.modules.information_access.review_models import Review
from app.modules.information_access.review_tools import ReviewTools


async def fence_review_write(request: Request, session: SessionDep, tenant_id: str = "local"):
    review_id = request.path_params.get("review_id")
    if review_id and request.method not in {"GET", "HEAD", "OPTIONS"}:
        try:
            await ReviewBatchService(session, request.path_params["project_id"], tenant_id).assert_review_writer(
                review_id, request.headers.get("X-Review-Worker"), request.headers.get("X-Review-Attempt"))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


router = APIRouter(prefix="/projects/{project_id}/reviews", tags=["review-state-tools"],
                   dependencies=[Depends(fence_review_write)])


def tools(session: SessionDep, project_id: str, tenant_id: str) -> ReviewTools:
    return ReviewTools(session, project_id, tenant_id)


def audit_tools(session: SessionDep, project_id: str, tenant_id: str) -> AuditService:
    return AuditService(session, project_id, tenant_id)




class CreateReview(BaseModel):
    tlr_run_id: str
    requirement_node_id: str


class CreateReviewBatch(BaseModel):
    tlr_run_id: str
    source_artifact_ids: list[str] = Field(min_length=1, max_length=100)
    target_artifact_ids: list[str] = Field(min_length=1, max_length=100)
    # Optional canonical-node scope.  When omitted, all nodes participating
    # in the selected TLR artifact scope are used.  Parent selections are
    # normalized to their participating descendants by ReviewTools.
    source_node_ids: list[str] = Field(default_factory=list, max_length=5000)
    target_node_ids: list[str] = Field(default_factory=list, max_length=5000)
    name: str = Field(default="需求—设计一致性分析", min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class SentenceRange(BaseModel):
    source_node_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class CheckInput(BaseModel):
    sentence_id: str
    statement: str = Field(min_length=1, max_length=2000)
    category: str = "function_result"
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, gt=0)


class EvidenceInput(BaseModel):
    check_id: str
    design_node_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    relation: str
    source: str = "agent_investigation"
    candidate_id: str | None = None
    exact_quote: str | None = None


class CheckUpdate(BaseModel):
    status: str
    note: str = ""


class VisitInput(BaseModel):
    design_node_id: str
    note: str = ""


class FinalizeChecks(BaseModel):
    check_ids: list[str] = Field(min_length=1)


class OpenPassage(BaseModel):
    sentence_id: str
    node_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    candidate_id: str | None = None


class PassageQuote(BaseModel):
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    exact_quote: str = Field(min_length=1)


class PassageDecision(BaseModel):
    check_id: str
    relation: str
    status: str
    note: str = Field(min_length=1, max_length=2000)
    quotes: list[PassageQuote] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def discard_unused_quotes(cls, value):
        if isinstance(value, dict) and value.get("relation") in {"irrelevant", "needs_context"}:
            return {**value, "quotes": []}
        return value


class AssessPassage(BaseModel):
    decisions: list[PassageDecision] = Field(min_length=1)
    finalize: bool = True


class PassageError(BaseModel):
    error_code: str = Field(min_length=1, max_length=64)
    error_message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False
    failure_id: str | None = Field(default=None, max_length=200)


class CheckConclusion(BaseModel):
    check_id: str
    status: str
    note: str = Field(min_length=1, max_length=2000)


class ConcludeInvestigation(BaseModel):
    conclusions: list[CheckConclusion] = Field(default_factory=list)
    finalize: bool = True
    review_pipeline: Literal["fixed_tlr", "full_investigation"] = "full_investigation"


class OverallEvidence(BaseModel):
    node_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    exact_quote: str = Field(min_length=1)
    candidate_id: str | None = None


class OverallIssue(BaseModel):
    source_quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: list[OverallEvidence] = Field(min_length=1)
    kind: str = "aggregate"
    statement: str = ""
    check_ids: list[str] = Field(default_factory=list)


class OverallProposal(BaseModel):
    status: str
    evaluation_text: str = Field(min_length=1)
    issues: list[OverallIssue] = Field(default_factory=list)


class OverallValidation(BaseModel):
    accepted: bool
    reason: str = Field(min_length=1)
    proposal_fingerprint: str = Field(min_length=1)


@router.post("/{review_id}/sentences/{sentence_id}/assessment/propose")
async def propose_overall_assessment(project_id: str, review_id: str, sentence_id: str,
                                     request: OverallProposal, session: SessionDep, tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).propose_assessment(
        review_id, sentence_id, request.status, request.evaluation_text,
        [issue.model_dump() for issue in request.issues])


@router.post("/{review_id}/execution-batch")
async def execution_batch(project_id: str, review_id: str, session: SessionDep, tenant_id: str = "local"):
    return await ReviewBatchService(session, project_id, tenant_id).execution_batch(review_id)


@router.post("/{review_id}/sentences/{sentence_id}/assessment/validate")
async def validate_overall_assessment(project_id: str, review_id: str, sentence_id: str,
                                      request: OverallValidation, session: SessionDep, tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).validate_assessment(
        review_id, sentence_id, request.accepted, request.reason, request.proposal_fingerprint)


@router.post("/{review_id}/sentences/{sentence_id}/finalize-checks")
async def finalize_checks(project_id: str, review_id: str, sentence_id: str,
                          request: FinalizeChecks, session: SessionDep, tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).finalize_checks(
        review_id, sentence_id, request.check_ids)


@router.post("/{review_id}/passages")
async def open_passage(project_id: str, review_id: str, request: OpenPassage,
                        session: SessionDep, tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).open_passage(
        review_id, request.sentence_id, request.node_id, request.start_offset,
        request.end_offset, request.candidate_id)


@router.post("/{review_id}/passages/{passage_id}/assess")
async def assess_passage(project_id: str, review_id: str, passage_id: str,
                          request: AssessPassage, session: SessionDep, tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).assess(
        review_id, passage_id, [d.model_dump() for d in request.decisions], finalize=request.finalize)


@router.post("/{review_id}/passages/{passage_id}/error")
async def passage_error(project_id: str, review_id: str, passage_id: str,
                         request: PassageError, session: SessionDep, tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).passage_error(
        review_id, passage_id, request.error_code, request.error_message,
        retryable=request.retryable, failure_id=request.failure_id)


@router.get("/{review_id}/sentences/{sentence_id}/investigation")
async def investigation_state(project_id: str, review_id: str, sentence_id: str,
                              session: SessionDep, tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).coverage(review_id, sentence_id)


@router.post("/{review_id}/sentences/{sentence_id}/conclude-investigation")
async def conclude_investigation(project_id: str, review_id: str, sentence_id: str,
                                 request: ConcludeInvestigation, session: SessionDep,
                                 tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).conclude(
        review_id, sentence_id, [d.model_dump() for d in request.conclusions], finalize=request.finalize,
        review_pipeline=request.review_pipeline)


@router.post("")
async def create_review(project_id: str, request: CreateReview, session: SessionDep,
                        tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).create_review(
        request.tlr_run_id, request.requirement_node_id)


@router.post("/batch")
async def create_review_batch(project_id: str, request: CreateReviewBatch, session: SessionDep,
                              tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).create_reviews(
        request.tlr_run_id, request.source_artifact_ids, request.target_artifact_ids,
        request.name, request.description, request.source_node_ids,
        request.target_node_ids)


@router.get("/batch/list")
async def list_batches(project_id: str, session: SessionDep, tenant_id: str = "local",
                       offset: int = 0, limit: int = 30):
    return await ReviewBatchService(session, project_id, tenant_id).list_batches(offset, limit)


@router.get("/batch/{batch_id}")
async def get_batch(project_id: str, batch_id: str, session: SessionDep, tenant_id: str = "local"):
    return await ReviewBatchService(session, project_id, tenant_id).detail(batch_id)


@router.post("/batch/{batch_id}/claim")
async def claim_batch(project_id: str, batch_id: str, session: SessionDep,
                      request: dict,
                      tenant_id: str = "local", resume: bool = False,
                      worker_token: str = Query(min_length=1, max_length=64)):
    from pydantic import TypeAdapter
    ids = request.get("review_ids")
    if ids is not None:
        ids = TypeAdapter(list[str]).validate_python(ids)
    return await ReviewBatchService(session, project_id, tenant_id).claim(batch_id, resume, worker_token, ids)


class BatchProgress(BaseModel):
    review_id: str
    result: dict
    worker_token: str = Field(min_length=1, max_length=64)


class BatchAttemptStart(BaseModel):
    review_id: str
    attempt_id: str = Field(min_length=1, max_length=64)
    worker_token: str = Field(min_length=1, max_length=64)


@router.post("/batch/{batch_id}/finish")
async def finish_batch(project_id: str, batch_id: str, session: SessionDep,
                      tenant_id: str = "local",
                      worker_token: str = Query(min_length=1, max_length=64)):
    return await ReviewBatchService(session, project_id, tenant_id).finish(batch_id, worker_token)


@router.post("/batch/{batch_id}/begin-review")
async def begin_batch_review(project_id: str, batch_id: str, request: BatchAttemptStart,
                             session: SessionDep, tenant_id: str = "local"):
    return await ReviewBatchService(session, project_id, tenant_id).begin_review_attempt(
        batch_id, request.review_id, request.attempt_id, request.worker_token)


@router.post("/batch/{batch_id}/progress")
async def batch_progress(project_id: str, batch_id: str, request: BatchProgress,
                         session: SessionDep, tenant_id: str = "local"):
    return await ReviewBatchService(session, project_id, tenant_id).progress(
        batch_id, request.review_id, request.result, request.worker_token)


@router.post("/batch/{batch_id}/interrupt")
async def interrupt_batch(project_id: str, batch_id: str, session: SessionDep,
                          tenant_id: str = "local",
                          worker_token: str = Query(min_length=1, max_length=64),
                          error_code: str = Query(default="worker_interrupted", min_length=1, max_length=128),
                          error_message: str = Query(default="Worker stopped before this execution finished",
                                                     min_length=1, max_length=2000)):
    return await ReviewBatchService(session, project_id, tenant_id).interrupt(
        batch_id, worker_token, error_code, error_message)


@router.post("/batch/{batch_id}/heartbeat")
async def heartbeat_batch(project_id: str, batch_id: str, session: SessionDep,
                          worker_token: str = Query(min_length=1, max_length=64), tenant_id: str = "local"):
    return await ReviewBatchService(session, project_id, tenant_id).heartbeat(batch_id, worker_token)


@router.post("/{review_id}/resume-passages")
async def resume_passages(project_id: str, review_id: str, session: SessionDep, tenant_id: str = "local"):
    return await PassageService(session, project_id, tenant_id).resume_blocked(review_id)


@router.get("")
async def list_reviews(project_id: str, session: SessionDep, tenant_id: str = "local",
                       tlr_run_id: str | None = None, requirement_node_id: str | None = None,
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100)):
    reader = tools(session, project_id, tenant_id)
    await reader.project()
    if tlr_run_id:
        await reader.run(tlr_run_id)
    if requirement_node_id:
        await reader.node(requirement_node_id)
    query = select(Review).where(Review.project_id == project_id)
    if tlr_run_id:
        query = query.where(Review.tlr_run_id == tlr_run_id)
    if requirement_node_id:
        query = query.where(Review.requirement_node_id == requirement_node_id)
    rows = list(await session.scalars(query.order_by(Review.created_at.desc(), Review.id)
                                     .offset(offset).limit(limit + 1)))
    nodes = {n.id: n for n in await session.scalars(select(ContentNode).where(
        ContentNode.id.in_([r.requirement_node_id for r in rows]))) }
    assessments = await audit_tools(session, project_id, tenant_id).assessment_projections(rows[:limit])
    has_more = len(rows) > limit
    return {"items": [{"review_id": r.id, "batch_id": r.batch_id, "tlr_run_id": r.tlr_run_id,
                       "requirement_node_id": r.requirement_node_id, "status": r.status,
                       "requirement_preview": (nodes[r.requirement_node_id].title
                                               or nodes[r.requirement_node_id].text)[:100]
                       if r.requirement_node_id in nodes else "",
                       "summary": r.summary_json, "assessment": assessments[r.id],
                       "created_at": r.created_at.isoformat()}
                      for r in rows[:limit]],
             "has_more": has_more, "next_offset": offset + limit if has_more else None}


@router.get("/{review_id}")
async def review_state(project_id: str, review_id: str, session: SessionDep,
                       tenant_id: str = "local", offset: int = 0,
                       limit: int = 50, mandatory_offset: int = 0,
                       mandatory_limit: int = 100):
    return await tools(session, project_id, tenant_id).review_state(
        review_id, offset=offset, limit=limit,
        mandatory_offset=mandatory_offset, mandatory_limit=mandatory_limit)


@router.post("/{review_id}/sentences")
async def add_sentence(project_id: str, review_id: str, request: SentenceRange,
                       session: SessionDep, tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).add_sentence(
        review_id, request.source_node_id, request.start_offset, request.end_offset)


@router.post("/{review_id}/checks")
async def add_check(project_id: str, review_id: str, request: CheckInput,
                    session: SessionDep, tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).add_check(
        review_id, request.sentence_id, request.statement, request.category,
        request.source_start, request.source_end)


@router.post("/{review_id}/evidence")
async def record_evidence(project_id: str, review_id: str, request: EvidenceInput,
                          session: SessionDep, tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).record_evidence(
        review_id, request.check_id, request.design_node_id,
        request.start_offset, request.end_offset, request.relation, request.source,
        request.candidate_id, request.exact_quote)


@router.patch("/{review_id}/checks/{check_id}")
async def update_check(project_id: str, review_id: str, check_id: str,
                       request: CheckUpdate, session: SessionDep,
                       tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).update_check(
        review_id, check_id, request.status, request.note)


@router.post("/{review_id}/mandatory-visits")
async def mark_mandatory_visited(project_id: str, review_id: str,
                                 request: VisitInput, session: SessionDep,
                                 tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).mark_mandatory_visited(
        review_id, request.design_node_id, request.note)


@router.post("/{review_id}/sentences/{sentence_id}/complete")
async def complete_sentence(project_id: str, review_id: str, sentence_id: str,
                            session: SessionDep, tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).complete_sentence(review_id, sentence_id)


@router.post("/{review_id}/complete")
async def complete_review(project_id: str, review_id: str, session: SessionDep,
                          tenant_id: str = "local"):
    return await tools(session, project_id, tenant_id).complete_review(review_id)


@router.post("/{review_id}/prepare")
async def prepare_review(project_id: str, review_id: str, session: SessionDep,
                         tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).prepare(review_id)


@router.get("/{review_id}/workspace")
async def audit_workspace(project_id: str, review_id: str, session: SessionDep,
                          tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).workspace(review_id)


@router.post("/{review_id}/summarize")
async def summarize_review(project_id: str, review_id: str, session: SessionDep,
                          tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).summarize(review_id)


@router.post("/{review_id}/verify")
async def verify_review(project_id: str, review_id: str, session: SessionDep,
                        tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).verify(review_id)


@router.post("/{review_id}/tasks/{task_id}/retry")
async def retry_review_task(project_id: str, review_id: str, task_id: str,
                            session: SessionDep, tenant_id: str = "local"):
    return await audit_tools(session, project_id, tenant_id).retry_task(
        review_id, task_id)
