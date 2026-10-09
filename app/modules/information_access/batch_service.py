"""Artifact-level task bookkeeping; cognitive work remains in the SDK service."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.modules.artifacts.models import ContentNode
from app.modules.information_access.review_models import (
    Review,
    ReviewBatch,
    ReviewEvent,
    ReviewTask,
)
from app.modules.information_access.service import InformationTools
from app.modules.information_access.state_machine import require_execution_status, set_state


class ReviewBatchService(InformationTools):
    async def _locked_batch(self, batch_id: str) -> ReviewBatch:
        await self.project()
        # Refresh the identity map AFTER acquiring the database lock. A get()
        # followed by FOR UPDATE without populate_existing can validate a
        # cached old token even after another transaction took ownership.
        row = await self.session.scalar(select(ReviewBatch).where(
            ReviewBatch.id == batch_id, ReviewBatch.project_id == self.project_id)
            .with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise ValueError("analysis task not found in project scope")
        return row
    @classmethod
    def _lease_deadline(cls) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=get_settings().review_worker_lease_seconds)

    @staticmethod
    def _lease_live(row: ReviewBatch) -> bool:
        deadline = row.lease_expires_at
        # SQLite round-trips lose timezone; production PostgreSQL retains it.
        if deadline is not None and deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return deadline is not None and deadline > datetime.now(UTC)

    @staticmethod
    def _assert_owner(row: ReviewBatch, worker_token: str | None, *, terminal: bool = False) -> None:
        """Reject writes from a stale worker after a batch has been claimed."""
        if not worker_token or not row.worker_token or worker_token != row.worker_token:
            raise ValueError("batch is owned by another worker")
        if not terminal and (row.status != "running" or not ReviewBatchService._lease_live(row)):
            raise ValueError("batch worker lease is not active")

    @classmethod
    def _touch_lease(cls, row: ReviewBatch) -> None:
        if row.worker_token is not None:
            row.lease_expires_at = cls._lease_deadline()

    async def batch(self, batch_id: str) -> ReviewBatch:
        await self.project()
        row = await self.session.get(ReviewBatch, batch_id)
        if row is None or row.project_id != self.project_id:
            raise ValueError("analysis task not found in project scope")
        return row

    @staticmethod
    def summary(row: ReviewBatch) -> dict:
        results = row.results_json or {}
        return {"batch_id": row.id, "name": row.name, "description": row.description,
                "tlr_run_id": row.tlr_run_id, "status": row.status,
                "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
                "execution_plan": {k: v for k, v in (row.execution_plan_json or {}).items() if k != "worker_epoch"},
                "scope": row.scope_json, "finished_count": sum(r.get("status") in {"completed", "idle", "partial", "blocked"} for r in results.values()),
                "status_counts": {status: sum(r.get("status") == status for r in results.values())
                                  for status in ("completed", "idle", "partial", "blocked", "running")},
                "execution_status_counts": {status: sum(r.get("status") == status for r in results.values())
                                             for status in ("completed", "idle", "partial", "blocked", "running")},
                "created_at": row.created_at.isoformat()}

    async def execution_batch(self, review_id: str) -> dict:
        """Attach an existing standalone workspace to the SAME durable executor.

        This creates bookkeeping only, not new checks, evidence or TLR data.
        An existing artifact-level batch is never silently widened or copied.
        """
        await self.project()
        review = await self.session.scalar(select(Review).where(
            Review.id == review_id, Review.project_id == self.project_id)
            .execution_options(populate_existing=True).with_for_update())
        if review is None:
            raise ValueError("review is outside this project")
        if review.batch_id is None:
            row = ReviewBatch(project_id=self.project_id, tlr_run_id=review.tlr_run_id,
                              name="单个语块一致性分析", scope_json=review.scope_json or {}, results_json={})
            self.session.add(row)
            await self.session.flush()
            review.batch_id = row.id
            await self.session.commit()
        return {"batch_id": review.batch_id, "review_id": review_id}

    async def list_batches(self, offset: int, limit: int) -> dict:
        await self.project()
        self._page(offset, limit)
        rows = list(await self.session.scalars(select(ReviewBatch).where(
            ReviewBatch.project_id == self.project_id).order_by(ReviewBatch.created_at.desc(), ReviewBatch.id)
            .offset(offset).limit(limit + 1)))
        has_more = len(rows) > limit
        return {"items": [self.summary(row) for row in rows[:limit]],
                "has_more": has_more, "next_offset": offset + limit if has_more else None}

    async def detail(self, batch_id: str) -> dict:
        row = await self.batch(batch_id)
        reviews = list(await self.session.scalars(select(Review).where(
            Review.batch_id == row.id).join(ContentNode, ContentNode.id == Review.requirement_node_id)
            .order_by(ContentNode.artifact_id, ContentNode.reading_order, Review.id)))
        nodes = {node.id: node for node in await self.session.scalars(select(ContentNode).where(
            ContentNode.id.in_([review.requirement_node_id for review in reviews])))}
        results = row.results_json or {}
        from app.modules.information_access.audit_service import AuditService

        assessments = await AuditService(self.session, self.project_id, self.tenant_id).assessment_projections(reviews)
        return {**self.summary(row), "review_ids": [review.id for review in reviews],
                "assessment_counts": {status: sum(a["status"] == status for a in assessments.values())
                                      for status in ("implemented", "uncovered", "issues", "analysis_error", "not_analyzed")},
                "semantic_status_counts": {status: sum(a.get("semantic_status") == status for a in assessments.values())
                                            for status in ("implemented", "uncovered", "issues")},
                "assessment_validity_counts": {status: sum(a.get("assessment_validity") == status for a in assessments.values())
                                                for status in ("accepted", "proposed", "stale", "rejected", "missing")},
                "reviews": [{"review_id": review.id, "tlr_run_id": review.tlr_run_id,
                             "requirement_node_id": review.requirement_node_id,
                             "artifact_id": nodes[review.requirement_node_id].artifact_id,
                             "requirement_preview": (nodes[review.requirement_node_id].title or
                                                      nodes[review.requirement_node_id].text)[:160],
                             "status": review.status, "execution": results.get(review.id),
                             "assessment": assessments[review.id],
                             "summary": review.summary_json, "created_at": review.created_at.isoformat()}
                            for review in reviews if review.requirement_node_id in nodes]}

    async def claim(self, batch_id: str, resume: bool = False,
                    worker_token: str | None = None, execution_review_ids: list[str] | None = None) -> dict:
        row = await self._locked_batch(batch_id)
        review_ids = set(await self.session.scalars(select(Review.id).where(Review.batch_id == row.id)))
        plan_ids = review_ids if execution_review_ids is None else set(execution_review_ids)
        if not plan_ids <= review_ids:
            raise ValueError("execution plan is outside the batch")
        token = worker_token or str(uuid4())
        if row.status == "running":
            if row.worker_token == token and self._lease_live(row):
                if set((row.execution_plan_json or {}).get("review_ids", [])) != plan_ids:
                    raise ValueError("cannot change the live worker execution plan")
                self._touch_lease(row)
                await self.session.commit()
                return {**(await self.detail(batch_id)), "worker_token": token}
            if self._lease_live(row):
                raise ValueError("analysis task is already owned by another live worker")
            if not resume:
                raise ValueError("analysis task is already running")
        if resume:
            # Explicit batch resume has the same workspace reopening semantics
            # as retry_task. A sentence may have been marked completed after
            # its checks were saved while the outer assessment/validation
            # still failed. Such a review is not semantically complete: an
            # execution error in results_json must reopen it even when its
            # review status was already ``completed``. Never reopen a review
            # whose overall proposal was independently accepted.
            reviews = list(await self.session.scalars(select(Review).where(
                Review.batch_id == row.id)))
            results = dict(row.results_json or {})
            for review in reviews:
                if review.id not in plan_ids:
                    continue
                execution = results.get(review.id) or {}
                if execution.get("status") == "running":
                    execution = {**execution, "status": "blocked", "error_code": "worker_interrupted",
                                 "error_message": "Previous execution interrupted before explicit batch resume"}
                    results[review.id] = execution
                    self.session.add(ReviewEvent(review_id=review.id, actor_role="manager",
                        event_type="batch_attempt_interrupted", status="blocked", payload_json=execution))
                overall = await self.session.scalar(select(ReviewTask).where(
                    ReviewTask.review_id == review.id,
                    ReviewTask.task_key.like("overall:%")))
                validation = (overall.output_json or {}).get("validation", {}) if overall else {}
                accepted = overall is not None and overall.status == "completed" \
                    and validation.get("accepted") is True
                resumable_error = bool(execution.get("error_code") or execution.get("error_message"))
                if review.status != "blocked" and not (resumable_error and not accepted):
                    continue
                previous_status = review.status
                set_state(review, "review", "prepared" if review.snapshot_json else "open",
                          reason="new_attempt")
                self.session.add(ReviewEvent(review_id=review.id, actor_role="manager",
                    event_type="batch_review_resumed", status="pending",
                    payload_json={"batch_id": row.id, "previous_status": previous_status,
                                  "reason": "execution_error_without_accepted_overall_assessment"}))
            row.results_json = results
        set_state(row, "batch", "running", reason="claim_or_resume")
        row.worker_token = token
        row.execution_plan_json = {"review_ids": sorted(plan_ids),
                                   "worker_epoch": hashlib.sha256(token.encode()).hexdigest()}
        row.lease_expires_at = self._lease_deadline()
        await self.session.commit()
        return {**(await self.detail(batch_id)), "worker_token": token}

    async def heartbeat(self, batch_id: str, worker_token: str) -> dict:
        row = await self._locked_batch(batch_id)
        self._assert_owner(row, worker_token)
        self._touch_lease(row)
        await self.session.commit()
        return {"batch_id": row.id, "status": row.status,
                "lease_seconds": get_settings().review_worker_lease_seconds}

    async def assert_review_writer(self, review_id: str, worker_token: str | None,
                                   attempt_id: str | None) -> None:
        """Fence EVERY review mutation in the same transaction as its write."""
        await self.project()
        review = await self.session.get(Review, review_id)
        if review is None or review.project_id != self.project_id:
            raise ValueError("review is outside this project")
        if review.batch_id is None:
            # Manual standalone workspaces have no batch worker to race.
            if worker_token or attempt_id:
                raise ValueError("review has no batch execution")
            return
        row = await self._locked_batch(review.batch_id)
        if row.worker_token is None and row.status != "running" and not (worker_token or attempt_id):
            return  # manual facts before the first execution only
        self._assert_owner(row, worker_token)
        current = (row.results_json or {}).get(review_id) or {}
        if current.get("status") != "running" or current.get("attempt_id") != attempt_id:
            raise ValueError("review attempt is not the current running attempt")

    async def progress(self, batch_id: str, review_id: str, result: dict,
                       worker_token: str | None = None) -> dict:
        row = await self._locked_batch(batch_id)
        self._assert_owner(row, worker_token)
        review = await self.session.get(Review, review_id)
        if review is None or review.batch_id != row.id:
            raise ValueError("review is outside this artifact-level task")
        require_execution_status(result.get("status"))
        if result.get("status") not in {"completed", "idle", "partial", "blocked"}:
            raise ValueError("invalid review execution status")
        incoming_attempt = result.get("attempt")
        incoming_attempt_id = result.get("attempt_id")
        existing_execution = (row.results_json or {}).get(review_id) or {}
        existing_attempt = existing_execution.get("attempt")
        existing_attempt_id = existing_execution.get("attempt_id")
        if (not existing_attempt_id or incoming_attempt_id != existing_attempt_id
                or incoming_attempt != existing_attempt):
            raise ValueError("result does not belong to the current review attempt")
        # Execution status is not a semantic satisfaction verdict. Preserve
        # existing evidence/check results regardless of this execution result.
        execution = {key: result.get(key) for key in
                     ("status", "error_code", "error_message", "problem_sentence_count", "sentence_errors",
                      "attempt", "attempt_id")}
        execution["started_at"] = existing_execution.get("started_at")
        execution["finished_at"] = datetime.now(UTC).isoformat()
        # Accept old checkpoint transport only to recover its factual failures,
        # never to synthesize an assessment or mark an incomplete review complete.
        if execution["status"] in {"partial", "blocked"} and not execution["error_code"]:
            failures = [r for r in result.get("results", []) if r.get("execution_status") == "blocked"]
            if failures:
                execution["sentence_errors"] = [{k: r.get(k) for k in
                    ("sentence_id", "error_code", "error_message")} for r in failures]
                execution["error_code"] = "sentence_execution_failed"
                execution["error_message"] = "; ".join(
                    f"{r.get('sentence_id')}: {r.get('error_code')}: {r.get('error_message')}"
                    for r in failures)[:2000]
        if execution["error_message"]:
            execution["error_message"] = str(execution["error_message"])[:2000]
        execution["worker_epoch"] = hashlib.sha256(worker_token.encode()).hexdigest()
        if existing_execution.get("status") != "running":
            comparable = tuple(k for k in execution if k != "finished_at")
            if all(existing_execution.get(k) == execution.get(k) for k in comparable):
                return self.summary(row)
            raise ValueError("review attempt already has a different terminal result")
        row.results_json = {**(row.results_json or {}), review_id: execution}
        self._touch_lease(row)
        task = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == review_id, ReviewTask.task_key == "batch_execution"))
        if task is None:
            task = ReviewTask(review_id=review_id, task_key="batch_execution", role="manager")
            self.session.add(task)
        target_task_status = "completed" if execution["status"] in {"completed", "idle"} else "blocked"
        set_state(task, "task", target_task_status, reason="review_execution_finished")
        task.error_code, task.error_message = execution["error_code"], execution["error_message"]
        task.output_json = execution
        if task.status == "blocked" and review.status != "completed":
            set_state(review, "review", "blocked", reason="review_execution_failed")
        elif task.status == "completed" and review.status == "blocked":
            # A successful newer execution reopens the review workspace for
            # semantic validation; execution completion alone never marks the
            # requirement satisfied.
            set_state(review, "review", "prepared", reason="new_attempt")
        self.session.add(ReviewEvent(review_id=review_id, actor_role="manager",
                                     event_type="batch_review_finished", status=task.status,
                                     payload_json=execution))
        await self.session.commit()
        return self.summary(row)

    async def finish(self, batch_id: str, worker_token: str | None = None) -> dict:
        """Only the worker that exhausted the task list declares batch completion.

        Existing results from a previous run must not complete a resumed batch
        after its first newly finished review.
        """
        row = await self._locked_batch(batch_id)
        self._assert_owner(row, worker_token, terminal=row.status in {"completed", "partial"})
        if row.status in {"completed", "partial"}:
            return self.summary(row)
        ids = set(await self.session.scalars(select(Review.id).where(Review.batch_id == row.id)))
        results = row.results_json or {}
        if set(results) != ids or any(r.get("status") not in {"completed", "idle", "partial", "blocked"}
                                     for r in results.values()):
            raise ValueError("batch still contains unfinished review executions")
        epoch = hashlib.sha256(worker_token.encode()).hexdigest()
        plan = row.execution_plan_json or {}
        if plan.get("worker_epoch") != epoch:
            raise ValueError("execution plan does not belong to this worker")
        # Old failed results do not prove the CURRENT worker exhausted its
        # task list. Reuse only genuinely accepted successful reviews.
        reviews = list(await self.session.scalars(select(Review).where(Review.batch_id == row.id)))
        from app.modules.information_access.audit_service import AuditService
        assessments = await AuditService(self.session, self.project_id, self.tenant_id).assessment_projections(reviews)
        for review in reviews:
            if review.id not in plan.get("review_ids", []):
                # This is a previously TERMINAL outcome, retained as failure
                # where applicable. It is not promoted to a successful result.
                continue
            result = results[review.id]
            if result.get("worker_epoch") == epoch:
                continue
            if result.get("status") not in {"completed", "idle"} or not assessments[review.id].get("finalized"):
                raise ValueError("current worker has not processed every unfinished review")
        set_state(row, "batch", "completed" if all(r.get("status") in {"completed", "idle"}
                                                     for r in results.values()) else "partial",
                  reason="worker_exhausted_review_set")
        row.lease_expires_at = None
        await self.session.commit()
        return self.summary(row)

    async def begin_review_attempt(self, batch_id: str, review_id: str, attempt_id: str,
                                   worker_token: str | None = None) -> dict:
        """Commit execution identity BEFORE any model runs; retain old errors as events.

        The start operation is idempotent for an identical request ID. It does
        not clear check/evidence facts, passage errors or semantic validation.
        A prior batch transport error is no longer the current execution.
        """
        row = await self._locked_batch(batch_id)
        self._assert_owner(row, worker_token)
        review = await self.session.get(Review, review_id)
        if review is None or review.batch_id != row.id:
            raise ValueError("review is outside this artifact-level task")
        if review_id not in (row.execution_plan_json or {}).get("review_ids", []):
            raise ValueError("review is outside this worker execution plan")
        previous = (row.results_json or {}).get(review_id) or {}
        if previous.get("attempt_id") == attempt_id:
            return previous
        if previous.get("status") == "running":
            raise ValueError("review attempt is already running; resume the interrupted batch first")
        if row.status != "running":
            raise ValueError("batch must be claimed before starting a review")
        task = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == review_id, ReviewTask.task_key == "batch_execution"))
        if task is None:
            task = ReviewTask(review_id=review_id, task_key="batch_execution", role="manager", attempt=0)
            self.session.add(task)
        attempt = max(task.attempt or 0, previous.get("attempt") or 0) + 1
        execution = {"status": "running", "attempt": attempt, "attempt_id": attempt_id,
                     "started_at": datetime.now(UTC).isoformat(), "error_code": None, "error_message": None}
        self.session.add(ReviewEvent(review_id=review_id, actor_role="manager",
                                    event_type="batch_review_attempt_started", status="running",
                                    payload_json={**execution, "previous_execution": previous}))
        task.attempt, task.output_json = attempt, execution
        # A completed batch-execution task can be run again only as an
        # explicit new attempt.  Route that reset through the same lifecycle
        # table rather than silently overwriting the state.
        if task.status == "completed":
            set_state(task, "task", "pending", reason="new_attempt")
        set_state(task, "task", "running", reason="attempt_started")
        task.error_code = task.error_message = None
        row.results_json = {**(row.results_json or {}), review_id: execution}
        self._touch_lease(row)
        if review.status in {"blocked", "completed"}:
            set_state(review, "review", "prepared" if review.snapshot_json else "open",
                      reason="new_attempt")
        await self.session.commit()
        return execution

    async def interrupt(self, batch_id: str, worker_token: str | None = None,
                        error_code: str = "worker_interrupted",
                        error_message: str = "Worker stopped before this execution finished") -> dict:
        row = await self._locked_batch(batch_id)
        self._assert_owner(row, worker_token, terminal=True)
        if row.status == "running":
            # Execution control is host-owned. Do not leave an interrupted
            # attempt looking active, and never rewrite its semantic facts.
            results = dict(row.results_json or {})
            for review_id, previous in results.items():
                if previous.get("status") != "running":
                    continue
                execution = {**previous, "status": "blocked", "interrupted": True, "error_code": error_code[:128],
                             "error_message": error_message[:2000],
                             "finished_at": datetime.now(UTC).isoformat()}
                results[review_id] = execution
                task = await self.session.scalar(select(ReviewTask).where(
                    ReviewTask.review_id == review_id, ReviewTask.task_key == "batch_execution"))
                if task is not None:
                    set_state(task, "task", "blocked", reason="batch_interrupted")
                    task.output_json = execution
                    task.error_code, task.error_message = execution["error_code"], execution["error_message"]
                review = await self.session.get(Review, review_id)
                if review is not None and review.status != "completed":
                    set_state(review, "review", "blocked", reason="batch_interrupted")
                self.session.add(ReviewEvent(review_id=review_id, actor_role="manager",
                    event_type="batch_attempt_interrupted", status="blocked", payload_json=execution))
            row.results_json = results
            set_state(row, "batch", "interrupted", reason="worker_interrupted")
            row.lease_expires_at = None
            await self.session.commit()
        return self.summary(row)
