"""Deterministic review workspace service.

LLM reasoning is intentionally not implemented in this backend service. The
OpenAI Agents SDK review-analysis Agent owns point extraction, evidence loops
and status updates through explicit ReviewTools HTTP endpoints. This service
only binds a review to a completed TLR, creates source ranges, exposes durable
workspace state, and performs deterministic summary and verification.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from app.modules.artifacts.models import ContentNode
from app.modules.information_access.review_models import (
    Review,
    ReviewBatch,
    ReviewCheck,
    ReviewEvent,
    ReviewEvidence,
    ReviewEvidenceCheck,
    ReviewPassage,
    ReviewPassageAssessment,
    ReviewSentence,
    ReviewTask,
    ReviewVisit,
)
from app.modules.information_access.service import InformationTools
from app.modules.information_access.state_machine import set_state
from app.modules.traceability.models import TlrRun
from app.modules.traceability.projections import node_content


def _fingerprint(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


class AuditService(InformationTools):
    """Workspace boundary; no model provider or hidden reasoning lives here."""

    async def _review(self, review_id: str) -> Review:
        row = await self.session.get(Review, review_id)
        if row is None or row.project_id != self.project_id:
            raise ValueError("review not found in project scope")
        await self.run(row.tlr_run_id)
        return row

    async def _evidence_rows(self, review_id: str) -> list[dict]:
        rows = await self.session.execute(select(ReviewEvidence, ReviewEvidenceCheck).join(
            ReviewEvidenceCheck, ReviewEvidenceCheck.evidence_id == ReviewEvidence.id).where(
                ReviewEvidence.review_id == review_id).order_by(ReviewEvidence.id, ReviewEvidenceCheck.check_id))
        # A flat API projection preserves each association; the quote is stored once.
        return [{"evidence_id": e.id, "check_id": link.check_id,
                 "design_node_id": e.design_node_id, "start_offset": e.start_offset,
                 "end_offset": e.end_offset, "exact_quote": e.exact_quote,
                 "relation": link.relation, "source": e.source,
                 "candidate_id": e.candidate_id, "validation_status": e.validation_status}
                for e, link in rows]

    async def _event(self, review_id: str, role: str, event_type: str,
                     payload: dict, *, task_id: str | None = None,
                     status: str = "succeeded") -> None:
        self.session.add(ReviewEvent(review_id=review_id, task_id=task_id,
                                     actor_role=role, event_type=event_type,
                                     status=status, payload_json=payload))
        await self.session.flush()

    async def _task(self, review_id: str, key: str, role: str,
                    *, status: str = "pending",
                    parent_task_id: str | None = None) -> ReviewTask:
        row = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == review_id, ReviewTask.task_key == key))
        if row is None:
            row = ReviewTask(review_id=review_id, parent_task_id=parent_task_id,
                             task_key=key, role=role, status=status)
            self.session.add(row)
            await self.session.flush()
        return row

    @staticmethod
    def _assessment_fingerprint(review: Review, sentence: ReviewSentence,
                                checks: list[ReviewCheck], evidence: list[ReviewEvidence],
                                passages: list[ReviewPassage],
                                assessments: list[ReviewPassageAssessment]) -> str:
        """Bind overall reasoning to saved facts, not conversation/execution status."""
        value = {
            "tlr": review.tlr_result_fingerprint, "scope": review.scope_json,
            "source": [sentence.source_node_id, sentence.start_offset, sentence.end_offset,
                       sentence.text, sentence.checkset_version, sentence.checks_finalized],
            "checks": sorted((c.id, c.ordinal, c.statement, c.source_quote, c.status, c.note)
                             for c in checks),
            "evidence": sorted((e.id, e.design_node_id, e.start_offset, e.end_offset,
                                e.exact_quote, e.candidate_id or "", e.validation_status)
                               for e in evidence),
            "passages": sorted((p.id, p.design_node_id, p.start_offset, p.end_offset,
                                p.checkset_version, p.content_fingerprint, p.status)
                               for p in passages),
            "assessments": sorted((a.passage_id, a.check_id, a.relation, a.note,
                                   a.resulting_status, sorted(a.evidence_ids)) for a in assessments),
        }
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    async def assessment_projections(self, reviews: list[Review]) -> dict[str, dict]:
        """Bulk read: semantic results stay separate from old batch execution rows."""
        from app.modules.information_access.review_completion import conclusion_state
        if not reviews:
            return {}
        review_ids = [r.id for r in reviews]
        sentences = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id.in_(review_ids)).order_by(ReviewSentence.ordinal)))
        sentence_ids = [s.id for s in sentences]
        checks = list(await self.session.scalars(select(ReviewCheck).where(
            ReviewCheck.sentence_id.in_(sentence_ids)))) if sentence_ids else []
        evidence = list(await self.session.scalars(select(ReviewEvidence).where(
            ReviewEvidence.review_id.in_(review_ids))))
        evidence_links = list(await self.session.scalars(select(ReviewEvidenceCheck).where(
            ReviewEvidenceCheck.evidence_id.in_([e.id for e in evidence])))) if evidence else []
        passages = list(await self.session.scalars(select(ReviewPassage).where(
            ReviewPassage.sentence_id.in_(sentence_ids)))) if sentence_ids else []
        passage_ids = [p.id for p in passages]
        assessments = list(await self.session.scalars(select(ReviewPassageAssessment).where(
            ReviewPassageAssessment.passage_id.in_(passage_ids)))) if passage_ids else []
        tasks = list(await self.session.scalars(select(ReviewTask).where(
            ReviewTask.review_id.in_(review_ids))))
        referenced_ids = {s.source_node_id for s in sentences} | {e.design_node_id for e in evidence}
        canonical_nodes = {n.id: n for n in await self.session.scalars(select(ContentNode).where(
            ContentNode.id.in_(referenced_ids)))} if referenced_ids else {}
        canonical_texts = {n.id: n.text for n in canonical_nodes.values() if n.node_type == "paragraph"}
        table_artifacts = {n.artifact_id for n in canonical_nodes.values() if n.node_type == "table"}
        for artifact_id in table_artifacts:
            nodes = {n.id: n for n in await self._nodes(artifact_id)}
            relations = await self._relations(artifact_id)
            for node in canonical_nodes.values():
                if node.artifact_id == artifact_id and node.node_type == "table":
                    canonical_texts[node.id] = node_content(node, nodes, relations)
        runs = {r.id: r for r in await self.session.scalars(select(TlrRun).where(
            TlrRun.id.in_({r.tlr_run_id for r in reviews})))}
        batch_ids = {r.batch_id for r in reviews if r.batch_id}
        batches = {b.id: b for b in await self.session.scalars(select(ReviewBatch).where(
            ReviewBatch.id.in_(batch_ids)))} if batch_ids else {}
        result = {}
        for review in reviews:
            local_tasks = {t.task_key: t for t in tasks if t.review_id == review.id}
            execution = local_tasks.get("batch_execution")
            batch = batches.get(review.batch_id)
            saved_execution = ((batch.results_json or {}).get(review.id) or {}) if batch else {}
            saved_failure = saved_execution.get("status") in {"partial", "blocked", "failed"}
            items = []
            for sentence in (s for s in sentences if s.review_id == review.id):
                local_checks = [c for c in checks if c.sentence_id == sentence.id]
                local_passages = [p for p in passages if p.sentence_id == sentence.id]
                local_ids = {p.id for p in local_passages}
                task = local_tasks.get(f"overall:{sentence.id}")
                saved = task.output_json if task is not None else {}
                saved = saved or {}
                proposal = saved.get("proposal") or {}
                validation = saved.get("validation") or {}
                check_ids = {c.id for c in local_checks}
                evidence_ids = {link.evidence_id for link in evidence_links if link.check_id in check_ids}
                evidence_ids.update(q["evidence_id"] for issue in proposal.get("issues", [])
                                    for q in issue.get("evidence", []))
                current = self._assessment_fingerprint(
                    review, sentence, local_checks, [e for e in evidence if e.id in evidence_ids],
                    local_passages, [a for a in assessments if a.passage_id in local_ids])
                local_evidence = [e for e in evidence if e.id in evidence_ids]
                target_ids = set((review.scope_json or {}).get("target_artifact_ids") or
                                 (runs[review.tlr_run_id].config or {}).get("target_artifact_ids", []))
                source_text = canonical_texts.get(sentence.source_node_id, "")
                canonical_valid = (0 <= sentence.start_offset < sentence.end_offset <= len(source_text)
                                   and source_text[sentence.start_offset:sentence.end_offset] == sentence.text)
                unanchored_check_ids = [c.id for c in local_checks
                                        if not (c.source_quote and sentence.text.count(c.source_quote) == 1)]
                canonical_valid = canonical_valid and all(
                    e.design_node_id in canonical_texts
                    and canonical_nodes[e.design_node_id].artifact_id in target_ids
                    and 0 <= e.start_offset < e.end_offset <= len(canonical_texts[e.design_node_id])
                    and canonical_texts[e.design_node_id][e.start_offset:e.end_offset] == e.exact_quote
                    for e in local_evidence)
                conclusions = conclusion_state(local_tasks.get(f"analysis:{sentence.id}"), sentence,
                    local_checks, local_passages, [a for a in assessments if a.passage_id in local_ids])
                proposal_current = (bool(proposal) and saved.get("facts_fingerprint") == current
                                    and sentence.status == "completed" and sentence.checks_finalized
                                    and bool(local_checks) and not any(c.status == "pending" for c in local_checks)
                                    and canonical_valid and conclusions["conclusions_complete"])
                finalized = bool(validation.get("accepted")) and proposal_current
                analysis = local_tasks.get(f"analysis:{sentence.id}")
                failed = (analysis is not None and analysis.status in {"blocked", "failed"}) or (
                    task is not None and task.status in {"blocked", "failed"}) or (
                    execution is not None and execution.status in {"blocked", "failed"}) or saved_failure
                execution_status = (saved_execution.get("status")
                                    or (execution.status if execution is not None else None)
                                    or ("completed" if sentence.status == "completed" else "pending"))
                execution_error = {
                    "code": (saved_execution.get("error_code")
                             or (execution.error_code if execution is not None else None)
                             or (analysis.error_code if analysis is not None else None)),
                    "message": (saved_execution.get("error_message")
                                or (execution.error_message if execution is not None else None)
                                or (analysis.error_message if analysis is not None else None)),
                }
                execution_error = execution_error if any(execution_error.values()) else None
                # Keep a saved semantic proposal visible on a separate axis.
                # It is never treated as final until its facts and validation
                # are current, but an execution error must not erase it.
                proposal_status = proposal.get("status") if proposal else None
                if validation.get("accepted") is True and proposal_current:
                    assessment_validity = "accepted"
                elif validation.get("accepted") is False:
                    assessment_validity = "rejected"
                elif proposal_current:
                    assessment_validity = "proposed"
                elif proposal:
                    assessment_validity = "stale"
                else:
                    assessment_validity = "missing"
                if finalized:
                    status = proposal["status"]
                    text = proposal["evaluation_text"]
                    reason = None
                elif failed or (validation.get("accepted") and not canonical_valid):
                    status, reason = "analysis_error", "execution_failed" if failed else "canonical_content_changed"
                    failure = next((t for t in (task, analysis, execution) if t is not None
                                    and t.status in {"blocked", "failed"}), None)
                    failure_message = (failure.error_message if failure and failure.error_message else
                                       saved_execution.get("error_message"))
                    text = ("原始内容或逐字证据已变化，现有评价不能作为有效结论。" if not canonical_valid else
                            "分析执行异常，不能据此判断需求是否实现。") + (
                        f" {failure_message}" if failure_message else "")
                else:
                    status = "not_analyzed"
                    reason = ("cumulative_conclusions_pending" if not conclusions["conclusions_complete"]
                              else "awaiting_overall_assessment" if sentence.status == "completed" and not proposal
                              else "assessment_facts_changed" if proposal and saved.get("facts_fingerprint") != current
                              else "overall_assessment_rejected" if validation.get("accepted") is False
                              else "awaiting_overall_validation" if proposal else "investigation_pending")
                    text = {"awaiting_overall_assessment": "检查点调查已结束，尚未形成并审核语块整体评价。",
                            "cumulative_conclusions_pending": "检查点资料记录已保留，尚需逐项形成基于全部证据的综合结论。",
                            "assessment_facts_changed": "已保存的评审事实发生变化，整体评价需要重新审核。",
                            "overall_assessment_rejected": "整体评价尚未通过审核。",
                            "awaiting_overall_validation": "整体评价已保存，等待独立审核。",
                            "investigation_pending": "检查点尚在调查中，未形成最终分析结论。"}[reason]
                items.append({"sentence_id": sentence.id, "status": status,
                              "evaluation_text": text, "finalized": finalized,
                              "suggestion_required": finalized and status in {"issues", "uncovered"},
                              "issues": proposal.get("issues", []) if finalized else [],
                              "proposed_status": proposal.get("status"), "pending_reason": reason,
                              "proposal_fingerprint": saved.get("proposal_fingerprint"),
                              "proposal_current": proposal_current,
                              "conclusions_complete": conclusions["conclusions_complete"],
                              "remaining_conclusion_check_ids": conclusions["remaining_conclusion_check_ids"],
                              "proposal": proposal, "validation": validation,
                              "semantic_status": proposal_status,
                              "assessment_validity": assessment_validity,
                              "execution_status": execution_status,
                              "execution_error": execution_error,
                              "semantic_result_available": bool(proposal_status),
                              "semantic_result_final": finalized,
                              "source_anchor_status": ("complete" if local_checks and not unanchored_check_ids
                                                        else "incomplete"),
                              "source_unanchored_check_ids": unanchored_check_ids})
            if not items:
                failed = (execution is not None and execution.status in {"blocked", "failed"}) or saved_failure
                status = "analysis_error" if failed else "not_analyzed"
                aggregate = {"status": status, "evaluation_text": "分析执行异常，未形成有效评价。" if failed else "尚未分析。",
                             "finalized": False, "suggestion_required": False, "issues": [],
                             "semantic_status": None, "assessment_validity": "missing",
                             "execution_status": (saved_execution.get("status") if saved_execution else
                                                   execution.status if execution is not None else "pending"),
                             "execution_error": ({"code": saved_execution.get("error_code"),
                                                   "message": saved_execution.get("error_message")} if saved_execution else None),
                             "semantic_result_available": False, "semantic_result_final": False,
                             "source_anchor_status": "incomplete", "source_unanchored_check_ids": []}
            elif all(i["finalized"] for i in items):
                statuses = {i["status"] for i in items}
                status = ("implemented" if statuses == {"implemented"} else "uncovered" if statuses == {"uncovered"}
                          else "issues")
                aggregate = {"status": status, "evaluation_text": "\n\n".join(i["evaluation_text"] for i in items),
                             "finalized": True, "suggestion_required": status in {"issues", "uncovered"},
                             "issues": [issue for i in items for issue in i["issues"]],
                             "semantic_status": status, "assessment_validity": "accepted",
                             "execution_status": "completed" if all(i.get("execution_status") in {"completed", "idle"}
                                                                       for i in items) else "partial",
                             "execution_error": next((i.get("execution_error") for i in items
                                                       if i.get("execution_error")), None),
                             "semantic_result_available": True, "semantic_result_final": True,
                             "source_anchor_status": ("complete" if all(i.get("source_anchor_status") == "complete"
                                                                          for i in items) else "incomplete"),
                             "source_unanchored_check_ids": [check_id for i in items
                                                              for check_id in i.get("source_unanchored_check_ids", [])]}
            else:
                status = "analysis_error" if any(i["status"] == "analysis_error" for i in items) else "not_analyzed"
                aggregate = {"status": status, "evaluation_text": "\n\n".join(i["evaluation_text"] for i in items),
                             "finalized": False, "suggestion_required": False,
                             "issues": [issue for i in items if i["finalized"] for issue in i["issues"]],
                             "semantic_status": next((i.get("semantic_status") for i in items
                                                       if i.get("semantic_status")), None),
                             "assessment_validity": ("proposed" if any(i.get("assessment_validity") == "proposed"
                                                                         for i in items)
                                                     else "stale" if any(i.get("assessment_validity") == "stale"
                                                                         for i in items) else "missing"),
                             "execution_status": ("partial" if any(i.get("execution_status") in {"partial", "blocked", "failed"}
                                                                      for i in items) else
                                                   "completed" if all(i.get("execution_status") in {"completed", "idle"}
                                                                      for i in items) else "pending"),
                             "execution_error": next((i.get("execution_error") for i in items
                                                       if i.get("execution_error")), None),
                             "semantic_result_available": any(i.get("semantic_result_available") for i in items),
                             "semantic_result_final": False,
                             "source_anchor_status": ("complete" if all(i.get("source_anchor_status") == "complete"
                                                                          for i in items) else "incomplete"),
                             "source_unanchored_check_ids": [check_id for i in items
                                                              for check_id in i.get("source_unanchored_check_ids", [])]}
            result[review.id] = {**aggregate, "sentences": items}
        return result

    async def _overall_context(self, review_id: str, sentence_id: str):
        from app.modules.information_access.passage_service import PassageService

        review = await self._review(review_id)
        await self.session.scalar(select(Review).where(Review.id == review_id).with_for_update())
        sentence = await self.session.get(ReviewSentence, sentence_id)
        if sentence is None or sentence.review_id != review_id:
            raise ValueError("sentence not found in review")
        checks = list(await self.session.scalars(select(ReviewCheck).where(
            ReviewCheck.sentence_id == sentence_id).order_by(ReviewCheck.ordinal)))
        if sentence.status != "completed" or not sentence.checks_finalized or not checks or any(c.status == "pending" for c in checks):
            raise ValueError("overall assessment requires completed finalized nonempty check set")
        service = PassageService(self.session, self.project_id, self.tenant_id)
        coverage = await service.ensure_complete(review_id, sentence_id)
        if not coverage.get("conclusions_complete"):
            raise ValueError("overall assessment requires explicit cumulative conclusions for every check")
        # Prepared reviews are created by the current extraction workflow,
        # whose checks carry exact source quotes.  Refuse to create a new
        # overall semantic proposal when that anchor ledger is incomplete;
        # old hand-built workspaces remain readable but are not silently
        # upgraded into a trustworthy conclusion.
        if review.snapshot_json and coverage.get("source_anchor_status") != "complete":
            raise ValueError("source check anchors are incomplete")
        source = await self.node(sentence.source_node_id)
        source_text = node_content(source, {n.id: n for n in await self._nodes(source.artifact_id)},
                                   await self._relations(source.artifact_id))
        if source_text[sentence.start_offset:sentence.end_offset] != sentence.text:
            raise ValueError("canonical source changed since review preparation")
        # Even non-supported final conclusions must not carry stale/foreign evidence.
        task = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == review_id, ReviewTask.task_key == f"overall:{sentence_id}"))
        proposal = ((task.output_json or {}).get("proposal") or {}) if task else {}
        rows = await self._sentence_evidence(review_id, checks, proposal)
        for row in rows:
            await self._validate_overall_quote(review_id, {
                "node_id": row.design_node_id, "start_offset": row.start_offset,
                "end_offset": row.end_offset, "exact_quote": row.exact_quote,
                "candidate_id": row.candidate_id})
            if row.validation_status != "valid":
                raise ValueError("overall assessment contains unvalidated evidence")
        return review, sentence, checks, coverage

    async def _sentence_evidence(self, review_id: str, checks: list[ReviewCheck], proposal: dict) -> list[ReviewEvidence]:
        linked = set(await self.session.scalars(select(ReviewEvidenceCheck.evidence_id).where(
            ReviewEvidenceCheck.check_id.in_([c.id for c in checks]))))
        linked.update(q["evidence_id"] for issue in proposal.get("issues", []) for q in issue.get("evidence", []))
        return list(await self.session.scalars(select(ReviewEvidence).where(
            ReviewEvidence.review_id == review_id, ReviewEvidence.id.in_(linked)))) if linked else []

    async def _validate_overall_quote(self, review_id: str, quote: dict) -> None:
        from app.modules.information_access.passage_service import PassageService
        from app.modules.traceability.models import Candidate, TraceUnit

        service = PassageService(self.session, self.project_id, self.tenant_id)
        review = await self._review(review_id)
        node, text = await service._target_text(review_id, quote["node_id"])
        start, end = quote["start_offset"], quote["end_offset"]
        if not 0 <= start < end <= len(text) or text[start:end] != quote["exact_quote"]:
            raise ValueError("overall evidence quote is not exact canonical text at requested range")
        candidate_id = quote.get("candidate_id") or None
        if candidate_id:
            candidate = await self.session.get(Candidate, candidate_id)
            if candidate is None or candidate.run_id != review.tlr_run_id:
                raise ValueError("overall evidence candidate outside review TLR run")
            source = await self.session.get(TraceUnit, candidate.source_unit_id)
            target = await self.session.get(TraceUnit, candidate.target_unit_id)
            if source is None or target is None or source.node_id != review.requirement_node_id or target.node_id != node.id:
                raise ValueError("overall evidence candidate does not bind source and target")

    async def propose_assessment(self, review_id: str, sentence_id: str, status: str,
                                 evaluation_text: str, issues: list[dict]) -> dict:
        """Save SDK analysis proposal; it is never a final verdict before validation."""
        _, sentence, checks, coverage = await self._overall_context(review_id, sentence_id)
        if status not in {"implemented", "uncovered", "issues"} or not evaluation_text.strip():
            raise ValueError("overall proposal requires a semantic status and evaluation text")
        statuses = {c.status for c in checks}
        if status == "implemented" and (statuses != {"supported"} or issues):
            raise ValueError("implemented overall result conflicts with saved checks or issues")
        if status == "uncovered" and (statuses != {"insufficient"} or issues):
            raise ValueError("uncovered requires every check insufficient and no claimed conflicts")
        if status == "issues" and statuses == {"supported"} and not issues:
            raise ValueError("overall-only issue requires explicit source, canonical evidence and analysis")
        normalized = []
        for issue in issues:
            source_quote = issue.get("source_quote", "")
            if not source_quote.strip() or source_quote not in sentence.text:
                raise ValueError("overall issue source quote must be exact retained source text")
            if not issue.get("reason", "").strip() or not issue.get("evidence"):
                raise ValueError("overall issue requires analysis and canonical evidence")
            check_ids = issue.get("check_ids", [])
            if not set(check_ids) <= {c.id for c in checks}:
                raise ValueError("overall issue references foreign checks")
            evidence_refs = []
            for quote in issue["evidence"]:
                await self._validate_overall_quote(review_id, quote)
                row = await self.session.scalar(select(ReviewEvidence).where(
                    ReviewEvidence.review_id == review_id, ReviewEvidence.design_node_id == quote["node_id"],
                    ReviewEvidence.start_offset == quote["start_offset"], ReviewEvidence.end_offset == quote["end_offset"],
                    ReviewEvidence.candidate_id == (quote.get("candidate_id") or None)).limit(1))
                if row is None:
                    row = ReviewEvidence(review_id=review_id, design_node_id=quote["node_id"],
                        start_offset=quote["start_offset"], end_offset=quote["end_offset"],
                        exact_quote=quote["exact_quote"], candidate_id=quote.get("candidate_id") or None,
                        validation_status="valid", source="sdk_overall_analysis")
                    self.session.add(row)
                    await self.session.flush()
                evidence_refs.append({"evidence_id": row.id, "node_id": row.design_node_id,
                                      "start_offset": row.start_offset, "end_offset": row.end_offset,
                                      "exact_quote": row.exact_quote, "candidate_id": row.candidate_id})
            normalized.append({"issue_id": str(uuid4()), "source_quote": source_quote,
                               "reason": issue["reason"].strip(), "kind": issue.get("kind", "aggregate"),
                               "statement": issue.get("statement", ""), "check_ids": check_ids,
                               "evidence": evidence_refs})
        proposal = {"status": status, "evaluation_text": evaluation_text.strip(), "issues": normalized,
                    "investigation_scope": coverage}
        task = await self._task(review_id, f"overall:{sentence_id}", "review_analysis")
        # The fingerprint includes newly persisted overall quotations, not only check-linked ones.
        passages = list(await self.session.scalars(select(ReviewPassage).where(ReviewPassage.sentence_id == sentence_id)))
        passage_ids = [p.id for p in passages]
        assessments = list(await self.session.scalars(select(ReviewPassageAssessment).where(
            ReviewPassageAssessment.passage_id.in_(passage_ids)))) if passage_ids else []
        evidence = await self._sentence_evidence(review_id, checks, proposal)
        facts = self._assessment_fingerprint(await self._review(review_id), sentence, checks, evidence, passages, assessments)
        proposal_fingerprint = hashlib.sha256(json.dumps({"facts": facts, "proposal": proposal},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        task.output_json = {"schema_version": 1, "sentence_id": sentence_id,
                            "facts_fingerprint": facts, "proposal_fingerprint": proposal_fingerprint,
                            "proposal": proposal, "validation": {}}
        task.input_fingerprint = facts
        set_state(task, "task", "pending_validation", reason="overall_proposal_saved")
        task.error_code = task.error_message = None
        await self._event(review_id, "review_analysis", "overall_assessment_proposed", task.output_json, task_id=task.id)
        await self.session.commit()
        projection = await self.assessment_projections([await self._review(review_id)])
        return next(s for s in projection[review_id]["sentences"] if s["sentence_id"] == sentence_id)

    async def validate_assessment(self, review_id: str, sentence_id: str, accepted: bool,
                                  reason: str, proposal_fingerprint: str) -> dict:
        review, sentence, checks, _ = await self._overall_context(review_id, sentence_id)
        task = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == review_id, ReviewTask.task_key == f"overall:{sentence_id}"))
        if task is None or not task.output_json or task.output_json.get("proposal_fingerprint") != proposal_fingerprint:
            raise ValueError("overall validation references an absent or different proposal")
        passages = list(await self.session.scalars(select(ReviewPassage).where(ReviewPassage.sentence_id == sentence_id)))
        ids = [p.id for p in passages]
        assessments = list(await self.session.scalars(select(ReviewPassageAssessment).where(
            ReviewPassageAssessment.passage_id.in_(ids)))) if ids else []
        evidence = await self._sentence_evidence(review_id, checks, task.output_json["proposal"])
        facts = self._assessment_fingerprint(review, sentence, checks, evidence, passages, assessments)
        if task.output_json.get("facts_fingerprint") != facts:
            raise ValueError("overall assessment facts changed before validation")
        if not reason.strip():
            raise ValueError("overall validator requires explicit reasoning")
        proposal = task.output_json["proposal"]
        for issue in proposal.get("issues", []):
            if issue["source_quote"] not in sentence.text:
                raise ValueError("overall issue source quote is stale")
            for quote in issue["evidence"]:
                await self._validate_overall_quote(review_id, quote)
                row = await self.session.get(ReviewEvidence, quote["evidence_id"])
                if (row is None or row.review_id != review_id or row.validation_status != "valid"
                        or row.design_node_id != quote["node_id"] or row.start_offset != quote["start_offset"]
                        or row.end_offset != quote["end_offset"] or row.exact_quote != quote["exact_quote"]
                        or row.candidate_id != quote.get("candidate_id")):
                    raise ValueError("overall evidence ID does not bind saved canonical quotation")
        task.output_json = {**task.output_json, "validation": {"accepted": accepted,
                           "reason": reason.strip(), "actor_role": "suggestion_validator",
                           "proposal_fingerprint": proposal_fingerprint}}
        set_state(task, "task", "completed" if accepted else "revision_required",
                  reason="overall_validation_accepted" if accepted else "overall_validation_rejected")
        await self._event(review_id, "suggestion_validator", "overall_assessment_validated",
                          {"sentence_id": sentence_id, **task.output_json["validation"]},
                          task_id=task.id, status="succeeded" if accepted else "revision_required")
        await self.session.commit()
        projection = await self.assessment_projections([await self._review(review_id)])
        return next(s for s in projection[review_id]["sentences"] if s["sentence_id"] == sentence_id)

    @staticmethod
    def _split_ranges(text: str) -> list[tuple[int, int]]:
        """Keep one canonical source node intact; the Agent owns its checks."""
        return [(0, len(text))] if text.strip() else []

    async def prepare(self, review_id: str) -> dict:
        """Bind a workspace to one completed TLR and create source ranges."""
        review = await self._review(review_id)
        if review.status not in {"open", "prepared", "running"}:
            raise ValueError("review is not open for preparation")
        node = await self.node(review.requirement_node_id)
        if node.node_type not in {"paragraph", "table"}:
            raise ValueError("audit source must be a textual canonical node")
        nodes = {n.id: n for n in await self._nodes(node.artifact_id)}
        source_text = node_content(node, nodes, await self._relations(node.artifact_id))
        run = await self.run(review.tlr_run_id)
        selected_scope = review.scope_json or {
            "source_artifact_ids": [node.artifact_id],
            "target_artifact_ids": list((run.config or {}).get("target_artifact_ids", [])),
            "selection_mode": "single_source_node",
        }
        review.snapshot_json = {
            "tlr_run_id": run.id, "tlr_status": run.status,
            "tlr_result_status": run.result_status, "run_config": run.config,
            "requirement_node_id": node.id, "source_artifact_id": node.artifact_id,
            "selected_scope": selected_scope,
            "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        }
        review.tlr_result_fingerprint = _fingerprint(
            {"run_id": run.id, "config": run.config, "metrics": run.metrics,
             "node": node.id, "text": source_text})
        planner = await self._task(review_id, "planner", "manager", status="completed")
        existing = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id).order_by(ReviewSentence.ordinal)))
        if not existing:
            for ordinal, (start, end) in enumerate(self._split_ranges(source_text), start=1):
                sentence = ReviewSentence(review_id=review_id, source_node_id=node.id,
                                          ordinal=ordinal, start_offset=start, end_offset=end,
                                          text=source_text[start:end], segment_kind="sentence")
                self.session.add(sentence)
                await self.session.flush()
                existing.append(sentence)
        for sentence in existing:
            await self._task(review_id, f"analysis:{sentence.id}", "review_analysis",
                             status="delegated", parent_task_id=planner.id)
        await self._task(review_id, "summary", "suggestion", status="delegated",
                         parent_task_id=planner.id)
        await self._task(review_id, "verifier", "suggestion_validator",
                         status="delegated", parent_task_id=planner.id)
        review.plan_json = {
            "roles": ["manager", "review_analysis", "evidence_investigator",
                      "web_research", "suggestion", "suggestion_validator"],
            "segmentation": "one exact canonical source-node range; no punctuation split",
            "evidence_scope": "mandatory TLR candidates plus bounded BM25 investigation",
            "review_loop": "one SDS passage assessed against the complete finalized source check set",
            "llm_owner": "frontend OpenAI Agents SDK Runner",
        }
        set_state(review, "review", "prepared", reason="workspace_prepared")
        await self._event(review_id, "manager", "workspace_prepared", {
            "sentence_count": len(existing),
            "tlr_result_fingerprint": review.tlr_result_fingerprint,
            "llm_execution": "delegated_to_sdk_review_analysis_agent",
        }, task_id=planner.id)
        await self.session.commit()
        return await self.workspace(review_id)

    async def summarize(self, review_id: str) -> dict:
        review = await self._review(review_id)
        checks = list(await self.session.scalars(select(ReviewCheck).join(ReviewSentence).where(
            ReviewSentence.review_id == review_id)))
        counts = {status: sum(c.status == status for c in checks)
                  for status in ("pending", "supported", "contradicted", "insufficient", "uncertain")}
        summary = {
            "verification_point_count": len(checks), "status_counts": counts,
            "semantic_result": ("unresolved" if counts["pending"]
                                 else "contradicted" if counts["contradicted"]
                                 else "unresolved" if counts["insufficient"] or counts["uncertain"]
                                 else "supported" if counts["supported"] else "unresolved"),
            "note": "workflow completion is not requirement satisfaction",
        }
        summary["assessment"] = (await self.assessment_projections([review]))[review_id]
        review.summary_json = summary
        task = await self._task(review_id, "summary", "suggestion", status="pending")
        set_state(task, "task", "completed", reason="summary_saved")
        task.output_json = summary
        await self._event(review_id, "suggestion", "summary_updated", summary, task_id=task.id)
        await self.session.commit()
        return summary

    async def verify(self, review_id: str) -> dict:
        review = await self._review(review_id)
        sentences = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id)))
        checks = list(await self.session.scalars(select(ReviewCheck).join(ReviewSentence).where(
            ReviewSentence.review_id == review_id)))
        evidence = await self._evidence_rows(review_id)
        problems: list[str] = []
        if not sentences:
            problems.append("no_review_segments")
        if not checks:
            problems.append("no_verification_points")
        if any(c.status == "pending" for c in checks):
            problems.append("unresolved_verification_points")
        semantic_observations = (["uncovered_or_uncertain_verification_points"]
                                 if any(c.status in {"uncertain", "insufficient"} for c in checks) else [])
        if any(c.status == "supported" and not any(
            e["check_id"] == c.id and e["relation"] in {"supporting", "context"} and e["validation_status"] == "valid"
            for e in evidence)
               for c in checks):
            problems.append("supported_point_without_evidence")
        if any(c.status == "contradicted" and not any(
            e["check_id"] == c.id and e["relation"] == "counter" and e["validation_status"] == "valid"
            for e in evidence)
               for c in checks):
            problems.append("contradicted_point_without_evidence")
        from app.modules.information_access.passage_service import PassageService

        passage_service = PassageService(self.session, self.project_id, self.tenant_id)
        coverage = [await passage_service.coverage(review_id, s.id) for s in sentences]
        if any(not c["checks_finalized"] for c in coverage):
            problems.append("verification_point_set_not_finalized")
        if any(not c["can_stop_for_sufficient_evidence"] and
               (not c["passage_count"] or c["unfinished_passage_ids"] or c["mandatory_remaining_node_ids"]
                or c["blocked_passage_ids"])
               for c in coverage):
            problems.append("passage_all_check_coverage_incomplete")
        if any(not c["can_stop_for_sufficient_evidence"] and c["mandatory_remaining_node_ids"] for c in coverage):
            problems.append("mandatory_tlr_nodes_unvisited")
        overall = (await self.assessment_projections([review]))[review_id]
        if not overall["finalized"]:
            problems.append("overall_assessment_not_validated")
        structural_errors = {"supported_point_without_evidence",
                             "contradicted_point_without_evidence", "verification_point_set_not_finalized",
                             "passage_all_check_coverage_incomplete", "overall_assessment_not_validated"}
        result = {"valid": not problems, "problems": problems,
                  "semantic_observations": semantic_observations, "assessment": overall,
                  "completed_workflow": bool(sentences) and bool(checks) and not any(
                      c.status == "pending" for c in checks)
                  and all(c["can_stop_for_sufficient_evidence"] or not c["mandatory_remaining_node_ids"] for c in coverage)
                  and not structural_errors.intersection(problems),
                  "investigation_scope": coverage}
        task = await self._task(review_id, "verifier", "suggestion_validator", status="pending")
        set_state(task, "task", "completed", reason="verifier_saved")
        task.output_json = result
        set_state(review, "review", "completed" if result["valid"] else "prepared",
                  reason="verification_passed" if result["valid"] else "validation_failure")
        await self._event(review_id, "suggestion_validator", "workspace_verified", result,
                          task_id=task.id, status="succeeded" if result["valid"] else "failed")
        await self.session.commit()
        return result

    async def retry_task(self, review_id: str, task_id: str) -> dict:
        review = await self._review(review_id)
        task = await self.session.get(ReviewTask, task_id)
        if task is None or task.review_id != review_id:
            raise ValueError("task not found in review")
        if task.status not in {"blocked", "failed"}:
            raise ValueError("only failed tasks can be retried")
        set_state(task, "task", "delegated", reason="manual_retry")
        task.error_code, task.error_message = None, None
        if review.status == "blocked":
            set_state(review, "review", "prepared", reason="manual_retry")
        await self._event(review_id, "manager", "task_requeued",
                          {"task_key": task.task_key}, task_id=task.id)
        await self.session.commit()
        return {"task_id": task.id, "task_key": task.task_key, "status": task.status}

    async def workspace(self, review_id: str) -> dict:
        review = await self._review(review_id)
        tasks = list(await self.session.scalars(select(ReviewTask).where(
            ReviewTask.review_id == review_id).order_by(ReviewTask.created_at, ReviewTask.id)))
        events = list(await self.session.scalars(select(ReviewEvent).where(
            ReviewEvent.review_id == review_id).order_by(ReviewEvent.created_at, ReviewEvent.id).limit(200)))
        sentences = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id).order_by(ReviewSentence.ordinal)))
        checks = list(await self.session.scalars(select(ReviewCheck).join(ReviewSentence).where(
            ReviewSentence.review_id == review_id).order_by(ReviewSentence.ordinal, ReviewCheck.ordinal)))
        evidence = await self._evidence_rows(review_id)
        passages = list(await self.session.scalars(select(ReviewPassage).join(ReviewSentence).where(
            ReviewSentence.review_id == review_id).order_by(ReviewPassage.id)))
        assessments = list(await self.session.scalars(select(ReviewPassageAssessment).join(
            ReviewPassage).join(ReviewSentence).where(ReviewSentence.review_id == review_id)))
        visits = list(await self.session.scalars(select(ReviewVisit).where(
            ReviewVisit.review_id == review_id)))
        overall = (await self.assessment_projections([review]))[review_id]
        sentence_assessments = {s["sentence_id"]: s for s in overall["sentences"]}
        check_evidence_ids = {e["evidence_id"] for e in evidence}
        overall_evidence = list(await self.session.scalars(select(ReviewEvidence).where(
            ReviewEvidence.review_id == review_id, ReviewEvidence.source == "sdk_overall_analysis")))
        return {
            "source": "tlr_bound_review_workspace", "review_id": review.id,
            "batch_id": review.batch_id,
            "status": review.status, "tlr_run_id": review.tlr_run_id,
            "requirement_node_id": review.requirement_node_id,
            "scope": review.scope_json,
            "snapshot": review.snapshot_json, "plan": review.plan_json,
            "summary": review.summary_json,
            "assessment": overall,
            "sentences": [{"sentence_id": s.id, "ordinal": s.ordinal,
                           "source_node_id": s.source_node_id,
                           "start_offset": s.start_offset, "end_offset": s.end_offset,
                           "text": s.text, "status": s.status,
                           "segment_kind": s.segment_kind, "checkset_version": s.checkset_version,
                           "checks_finalized": s.checks_finalized,
                           "assessment": sentence_assessments[s.id],
                           "status_counts": {status: sum(c.sentence_id == s.id and c.status == status for c in checks)
                                             for status in ("pending", "supported", "contradicted", "insufficient", "uncertain")}}
                          for s in sentences],
            "checks": [{"check_id": c.id, "sentence_id": c.sentence_id,
                        "ordinal": c.ordinal, "category": c.category,
                        "statement": c.statement, "source_quote": c.source_quote,
                        "status": c.status, "note": c.note} for c in checks],
            "evidence": evidence,
            "overall_evidence": [{"evidence_id": e.id, "node_id": e.design_node_id,
                                  "start_offset": e.start_offset, "end_offset": e.end_offset,
                                  "exact_quote": e.exact_quote, "candidate_id": e.candidate_id,
                                  "validation_status": e.validation_status}
                                 for e in overall_evidence if e.id not in check_evidence_ids],
            "passages": [{"passage_id": p.id, "sentence_id": p.sentence_id,
                          "design_node_id": p.design_node_id, "candidate_id": p.candidate_id,
                          "start_offset": p.start_offset, "end_offset": p.end_offset,
                          "checkset_version": p.checkset_version, "status": p.status,
                          "error_code": p.error_code, "error_message": p.error_message,
                          "assessments": [{"check_id": a.check_id, "relation": a.relation,
                                           "note": a.note, "resulting_status": a.resulting_status,
                                           "evidence_ids": a.evidence_ids}
                                          for a in assessments if a.passage_id == p.id]} for p in passages],
            "visits": [{"design_node_id": v.design_node_id, "note": v.note} for v in visits],
            "tasks": [{"task_id": t.id, "task_key": t.task_key, "role": t.role,
                       "status": t.status, "attempt": t.attempt,
                       "output": t.output_json, "error_code": t.error_code,
                       "error_message": t.error_message} for t in tasks],
            "events": [{"event_id": e.id, "actor_role": e.actor_role,
                        "event_type": e.event_type, "status": e.status,
                        "payload": e.payload_json} for e in events],
        }
