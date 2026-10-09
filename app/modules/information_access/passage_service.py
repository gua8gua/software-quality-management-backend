"""Deterministic, evidence-first checkpoints inside the existing review workspace.

The SDK Agent owns interpretation. This service enforces range ownership,
quotation validity, all-check coverage and transactional business-state writes.
"""

import hashlib

from sqlalchemy import select

from app.modules.information_access.review_completion import conclusion_state
from app.modules.information_access.review_models import (
    Review,
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
from app.modules.information_access.review_tools import ReviewTools
from app.modules.information_access.state_machine import set_state
from app.modules.traceability.models import Candidate, TraceUnit
from app.modules.traceability.projections import node_content


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def covers_text(ranges: list[tuple[int, int]], length: int) -> bool:
    """Range union, not a count of reads, proves coverage of a canonical node."""
    end = 0
    for start, stop in sorted(ranges):
        if start > end:
            return False
        end = max(end, stop)
    return end >= length and bool(ranges)


class PassageService(ReviewTools):
    async def _locked_sentence(self, review_id: str, sentence_id: str) -> ReviewSentence:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is closed")
        # Shared quotation reuse is serialized across all sentences of one review.
        await self.session.scalar(select(Review).where(Review.id == review_id).with_for_update())
        await self._sentence(review_id, sentence_id)
        return await self.session.scalar(select(ReviewSentence).where(
            ReviewSentence.id == sentence_id).with_for_update())

    async def _checks(self, sentence_id: str) -> list[ReviewCheck]:
        return list(await self.session.scalars(select(ReviewCheck).where(
            ReviewCheck.sentence_id == sentence_id).order_by(ReviewCheck.ordinal)))

    async def conclusions(self, sentence: ReviewSentence, checks: list[ReviewCheck]) -> dict:
        task = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == sentence.review_id, ReviewTask.task_key == f"analysis:{sentence.id}"))
        passages = list(await self.session.scalars(select(ReviewPassage).where(
            ReviewPassage.sentence_id == sentence.id)))
        assessments = list(await self.session.scalars(select(ReviewPassageAssessment).where(
            ReviewPassageAssessment.passage_id.in_([p.id for p in passages])))) if passages else []
        return conclusion_state(task, sentence, checks, passages, assessments)

    async def finalize_checks(self, review_id: str, sentence_id: str,
                              check_ids: list[str]) -> dict:
        sentence = await self._locked_sentence(review_id, sentence_id)
        actual = {c.id for c in await self._checks(sentence_id)}
        if not actual or set(check_ids) != actual or len(check_ids) != len(actual):
            raise ValueError("finalized set must contain every existing check exactly once")
        sentence.checks_finalized = True
        self.session.add(ReviewEvent(review_id=review_id, actor_role="review_analysis",
                                    event_type="checkset_finalized", payload_json={
                                        "sentence_id": sentence_id, "check_ids": sorted(actual),
                                        "checkset_version": sentence.checkset_version}))
        await self.session.commit()
        return {"sentence_id": sentence_id, "checks_finalized": True,
                "checkset_version": sentence.checkset_version, "check_ids": sorted(actual)}

    async def _target_text(self, review_id: str, node_id: str) -> tuple[object, str]:
        review = await self._review(review_id)
        run = await self.run(review.tlr_run_id)
        node = await self.node(node_id)
        target_ids = (review.scope_json or {}).get("target_artifact_ids") or (run.config or {}).get("target_artifact_ids", [])
        if node.artifact_id not in set(target_ids):
            raise ValueError("passage outside TLR target artifacts")
        target_node_ids = set((review.scope_json or {}).get("target_node_ids", []))
        if target_node_ids and node.id not in target_node_ids:
            raise ValueError("passage outside selected TLR node scope")
        if node.node_type not in {"paragraph", "table"}:
            raise ValueError("passage requires canonical paragraph or table; no image understanding")
        text = (node_content(node, {n.id: n for n in await self._nodes(node.artifact_id)},
                             await self._relations(node.artifact_id))
                if node.node_type == "table" else node.text)
        return node, text

    async def open_passage(self, review_id: str, sentence_id: str, node_id: str,
                           start: int, end: int, candidate_id: str | None = None) -> dict:
        # SDK tool schemas may serialize an omitted optional candidate binding as
        # an empty string.  Treat that representation as the absent binding;
        # persisting it would violate the nullable candidate foreign key and
        # turn a recoverable read into a 500 error.
        candidate_id = candidate_id or None
        sentence = await self._locked_sentence(review_id, sentence_id)
        if not sentence.checks_finalized:
            raise ValueError("finalize complete check set before passage investigation")
        if sentence.status == "completed":
            raise ValueError("sentence is closed")
        node, text = await self._target_text(review_id, node_id)
        if not 0 <= start < end <= len(text):
            raise ValueError("passage range outside canonical text")
        if candidate_id:
            review = await self._review(review_id)
            candidate = await self.session.get(Candidate, candidate_id)
            if candidate is None or candidate.run_id != review.tlr_run_id:
                raise ValueError("candidate outside review TLR run")
            source = await self.session.get(TraceUnit, candidate.source_unit_id)
            target = await self.session.get(TraceUnit, candidate.target_unit_id)
            if source is None or target is None or source.node_id != review.requirement_node_id or target.node_id != node.id:
                raise ValueError("candidate does not bind source and passage")
        row = await self.session.scalar(select(ReviewPassage).where(
            ReviewPassage.sentence_id == sentence_id, ReviewPassage.design_node_id == node_id,
            ReviewPassage.start_offset == start, ReviewPassage.end_offset == end,
            ReviewPassage.checkset_version == sentence.checkset_version))
        if row is None:
            row = ReviewPassage(sentence_id=sentence_id, design_node_id=node_id,
                                start_offset=start, end_offset=end, candidate_id=candidate_id,
                                checkset_version=sentence.checkset_version,
                                content_fingerprint=fingerprint(text))
            self.session.add(row)
            await self.session.flush()
        if row.content_fingerprint != fingerprint(text):
            raise ValueError("canonical target text changed")
        if row.candidate_id != candidate_id:
            raise ValueError("passage candidate binding differs from saved binding")
        # Merely reading an interrupted passage does not erase its execution error.
        await self.session.commit()
        return {"passage_id": row.id, "node_id": node.id, "candidate_id": row.candidate_id,
                "start_offset": start, "end_offset": end, "text": text[start:end],
                "node_text_length": len(text), "provenance": node.provenance_json,
                "checkset_version": row.checkset_version, "status": row.status}

    async def assess(self, review_id: str, passage_id: str, decisions: list[dict],
                     *, finalize: bool = True) -> dict:
        """Atomically save a passage's assessments, quotes and current check states.

        Groups may be committed separately. Completion requires all checks,
        regardless of grouping; no missing judgment is synthesized by the service.
        """
        passage = await self.session.get(ReviewPassage, passage_id)
        if passage is None:
            raise ValueError("passage not found")
        sentence = await self._locked_sentence(review_id, passage.sentence_id)
        # Refresh after acquiring the shared lock: a parallel failure recorder
        # may have blocked this range while this session waited for the lock.
        await self.session.refresh(passage)
        if sentence.status == "completed":
            raise ValueError("sentence is closed")
        if not sentence.checks_finalized or passage.checkset_version != sentence.checkset_version:
            raise ValueError("passage check set is not current")
        if passage.status == "blocked":
            raise ValueError("passage is blocked; resume explicitly before assessment")
        _, text = await self._target_text(review_id, passage.design_node_id)
        if fingerprint(text) != passage.content_fingerprint:
            raise ValueError("canonical target text changed")
        checks = {c.id: c for c in await self._checks(sentence.id)}
        submitted = [d["check_id"] for d in decisions]
        if not decisions or len(submitted) != len(set(submitted)) or not set(submitted) <= checks.keys():
            raise ValueError("assessment has duplicate, missing or foreign check IDs")
        try:
            for decision in decisions:
                check_id = decision["check_id"]
                relation = decision["relation"]
                if relation not in {"supporting", "counter", "context", "irrelevant", "needs_context"}:
                    raise ValueError("invalid passage relation")
                # An irrelevant/context-needed judgment has no canonical evidence.
                # Discard unused quote fields before validating their shape/offsets.
                quotes = [] if relation in {"irrelevant", "needs_context"} else decision.get("quotes", [])
                if relation in {"supporting", "counter", "context"} and not quotes:
                    raise ValueError("evidence relation requires exact canonical quotation")
                note = decision["note"]
                if not note.strip() or len(note) > 2000:
                    raise ValueError("assessment requires an explicit bounded reason")
                existing = await self.session.scalar(select(ReviewPassageAssessment).where(
                    ReviewPassageAssessment.passage_id == passage_id,
                    ReviewPassageAssessment.check_id == check_id))
                if existing is not None:
                    saved_quotes = list(await self.session.scalars(select(ReviewEvidence).where(
                        ReviewEvidence.id.in_(existing.evidence_ids))))
                    actual_quotes = sorted((e.start_offset, e.end_offset, e.exact_quote) for e in saved_quotes)
                    requested_quotes = sorted((q["start_offset"], q["end_offset"], q["exact_quote"]) for q in quotes)
                    if (existing.relation != relation or existing.note != note
                            or existing.resulting_status != decision["status"] or actual_quotes != requested_quotes):
                        raise ValueError("saved passage assessment is immutable; do not silently overwrite facts")
                    # Lost-response replay must not rewind a check updated by a later passage.
                    continue
                evidence_ids = []
                for quote in quotes:
                    start, end = quote["start_offset"], quote["end_offset"]
                    if not passage.start_offset <= start < end <= passage.end_offset:
                        raise ValueError("evidence range outside investigated passage")
                    result = await self.record_evidence(
                        review_id, check_id, passage.design_node_id, start, end, relation,
                        "sdk_passage_review", passage.candidate_id, quote["exact_quote"], commit=False)
                    evidence_ids.append(result["evidence_id"])
                await self.update_check(review_id, check_id, decision["status"], note, commit=False)
                payload = {"relation": relation, "note": note,
                           "resulting_status": decision["status"], "evidence_ids": evidence_ids}
                self.session.add(ReviewPassageAssessment(passage_id=passage_id, check_id=check_id, **payload))
                await self.session.flush()
            assessed = {r.check_id for r in await self.session.scalars(select(ReviewPassageAssessment).where(
                ReviewPassageAssessment.passage_id == passage_id))}
            if finalize and assessed != checks.keys() and not await self.all_supported(review_id, sentence.id):
                raise ValueError("passage completion requires assessment of every check")
            # Sufficient evidence can terminate the review before this particular
            # passage has all-check coverage; never label that passage completed.
            if assessed == checks.keys():
                set_state(passage, "passage", "completed", reason="all_checks_assessed")
            elif passage.status != "pending":
                set_state(passage, "passage", "pending", reason="partial_check_assessment")
            passage.error_code = passage.error_message = None
            review = await self._review(review_id)
            set_state(review, "review", "running", reason="passage_facts_updated")
            counts = {s: sum(c.status == s for c in checks.values()) for s in
                      ("pending", "supported", "contradicted", "insufficient", "uncertain")}
            progress = {"sentence_id": sentence.id, "passage_id": passage.id,
                        "checkset_version": sentence.checkset_version,
                        "assessed_check_ids": sorted(assessed),
                        "remaining_check_ids": sorted(checks.keys() - assessed),
                        "passage_status": passage.status, "status_counts": counts}
            covered = await self.coverage(review_id, sentence.id)
            progress.update(covered)
            for node_id in covered["mandatory_assessed_node_ids"]:
                visit = await self.session.scalar(select(ReviewVisit).where(
                    ReviewVisit.review_id == review_id, ReviewVisit.design_node_id == node_id))
                if visit is None:
                    self.session.add(ReviewVisit(review_id=review_id, design_node_id=node_id,
                                                note="Exact canonical ranges assessed against every finalized check"))
            all_checks = list(await self.session.scalars(select(ReviewCheck).join(ReviewSentence).where(
                ReviewSentence.review_id == review_id)))
            review.summary_json = {"verification_point_count": len(all_checks),
                                   "status_counts": {s: sum(c.status == s for c in all_checks) for s in counts},
                                   "provisional": True,
                                   "note": "updated facts; not a declaration of completed requirement satisfaction"}
            self.session.add(ReviewEvent(review_id=review_id, actor_role="review_analysis",
                                        event_type="passage_assessed", payload_json=progress))
            await self.session.commit()
            return progress
        except Exception:
            await self.session.rollback()
            raise

    async def resume_blocked(self, review_id: str) -> dict:
        """Host-controlled recovery; retain prior errors and valid partial facts."""
        await self._review(review_id)
        sentences = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id)))
        resumed, unchanged, reasons = [], [], {}
        for sentence in sentences:
            if sentence.status == "completed":
                continue
            await self._locked_sentence(review_id, sentence.id)
            passages = list(await self.session.scalars(select(ReviewPassage).where(
                ReviewPassage.sentence_id == sentence.id, ReviewPassage.status == "blocked")))
            for passage in passages:
                failures = await self._passage_failures(review_id, passage.id)
                if any(e.payload_json.get("skipped") for e in failures):
                    unchanged.append(passage.id)
                    reasons[passage.id] = "local retry exhausted; preserved as skipped execution failure"
                    continue
                try:
                    _, text = await self._target_text(review_id, passage.design_node_id)
                except ValueError as exc:
                    unchanged.append(passage.id)
                    reasons[passage.id] = str(exc)
                    continue
                if (not sentence.checks_finalized or passage.checkset_version != sentence.checkset_version
                        or passage.content_fingerprint != fingerprint(text)):
                    unchanged.append(passage.id)
                    reasons[passage.id] = "canonical text or finalized check set differs from the saved passage"
                    continue
                self.session.add(ReviewEvent(review_id=review_id, actor_role="host",
                    event_type="passage_execution_resumed", status="pending", payload_json={
                        "passage_id": passage.id, "previous_error_code": passage.error_code,
                        "previous_error_message": passage.error_message,
                        "checkset_version": passage.checkset_version,
                        "content_fingerprint": passage.content_fingerprint}))
                set_state(passage, "passage", "pending", reason="resume_blocked_passage")
                passage.error_code = passage.error_message = None
                resumed.append(passage.id)
        await self.session.commit()
        return {"resumed_passage_ids": resumed, "unchanged_passage_ids": unchanged,
                "unchanged_reasons": reasons}

    async def _passage_failures(self, review_id: str, passage_id: str) -> list:
        events = list(await self.session.scalars(select(ReviewEvent).where(
            ReviewEvent.review_id == review_id,
            ReviewEvent.event_type == "passage_execution_failed")))
        return [event for event in events if event.payload_json.get("passage_id") == passage_id]

    async def passage_error(self, review_id: str, passage_id: str, code: str, message: str,
                            *, retryable: bool = False, failure_id: str | None = None) -> dict:
        passage = await self.session.get(ReviewPassage, passage_id)
        if passage is None:
            raise ValueError("passage not found")
        await self._locked_sentence(review_id, passage.sentence_id)
        await self.session.refresh(passage)
        events = await self._passage_failures(review_id, passage_id)
        # Recording a lost-response replay must not consume the one local retry.
        if failure_id:
            prior = next((e for e in events if e.payload_json.get("failure_id") == failure_id), None)
            if prior:
                return {**prior.payload_json, "error_recorded": True, "replayed": True}
        retried = any(e.payload_json.get("retryable") for e in events)
        retry_allowed = retryable and not retried and passage.status == "pending"
        # A completed range remains completed. Later transport/model failures
        # are observable events, never a reason to erase successfully saved facts.
        completed = passage.status == "completed"
        if not completed:
            if not retry_allowed:
                set_state(passage, "passage", "blocked", reason="execution_error")
            passage.error_code, passage.error_message = code, message
        result = {"passage_id": passage_id,
                  "execution_status": "completed" if completed else "retry_required" if retry_allowed else "blocked",
                  "error_code": code, "error_message": message,
                  "retryable": retryable, "retry_allowed": retry_allowed,
                  "retry_limit": 1, "skipped": retryable and not completed and not retry_allowed,
                  "failure_id": failure_id}
        self.session.add(ReviewEvent(review_id=review_id, actor_role="review_analysis",
                                    event_type="passage_execution_failed", status="failed",
                                    payload_json=result))
        await self.session.commit()
        return {**result, "error_recorded": True}

    async def coverage(self, review_id: str, sentence_id: str) -> dict:
        sentence = await self._sentence(review_id, sentence_id)
        checks = await self._checks(sentence_id)
        passages = list(await self.session.scalars(select(ReviewPassage).where(
            ReviewPassage.sentence_id == sentence_id,
            ReviewPassage.checkset_version == sentence.checkset_version)))
        mandatory = await self._mandatory_ids(await self._review(review_id))
        covered = set()
        attempted = set()
        for node_id in mandatory:
            _, text = await self._target_text(review_id, node_id)
            attempted_ranges = [(p.start_offset, p.end_offset) for p in passages
                                if p.design_node_id == node_id and p.content_fingerprint == fingerprint(text)]
            if covers_text(attempted_ranges, len(text)):
                attempted.add(node_id)
            complete = [p for p in passages if p.design_node_id == node_id and p.status == "completed"]
            if not complete:
                continue
            ranges = [(p.start_offset, p.end_offset) for p in complete
                      if p.content_fingerprint == fingerprint(text)]
            if covers_text(ranges, len(text)):
                covered.add(node_id)
        all_supported = await self.all_supported(review_id, sentence_id)
        # This is an integrity diagnostic for the extraction stage, not a
        # claim that quoted spans exhaustively capture every semantic duty in
        # the source.  A real prepared review may only be finalized when each
        # check remains anchored to one exact, unique source quote.  Keeping
        # the diagnostic in the persisted coverage response makes stale or
        # legacy workspaces observable without inventing a semantic result.
        unanchored = []
        anchored_ranges = []
        for check in checks:
            quote = (check.source_quote or "").strip()
            if not quote or sentence.text.count(quote) != 1:
                unanchored.append(check.id)
                continue
            start = sentence.text.index(quote) + sentence.start_offset
            anchored_ranges.append((start, start + len(quote)))
        source_anchor_status = "complete" if checks and not unanchored else "incomplete"
        return {**(await self.conclusions(sentence, checks)),
                "sentence_id": sentence_id, "checks_finalized": sentence.checks_finalized,
                "checkset_version": sentence.checkset_version,
                "source_anchor_status": source_anchor_status,
                "source_anchor_check_ids": [c.id for c in checks if c.id not in unanchored],
                "source_unanchored_check_ids": sorted(unanchored),
                "source_anchor_ranges": sorted(anchored_ranges),
                "source_text_length": len(sentence.text),
                "all_checks_supported": all_supported,
                "can_stop_for_sufficient_evidence": sentence.checks_finalized and all_supported,
                "unexamined_reason": "stopped_after_sufficient_evidence" if sentence.checks_finalized and all_supported else None,
                "mandatory_assessed_node_ids": sorted(covered),
                "mandatory_remaining_node_ids": sorted(mandatory - covered),
                "passage_count": len(passages),
                "mandatory_unattempted_node_ids": sorted(mandatory - attempted),
                "mandatory_failed_node_ids": sorted((mandatory - covered) & attempted),
                "blocked_passage_ids": [p.id for p in passages if p.status == "blocked"],
                "execution_errors": [{"passage_id": p.id, "error_code": p.error_code,
                                      "error_message": p.error_message} for p in passages if p.status == "blocked"],
                "unfinished_passage_ids": [p.id for p in passages if p.status == "pending"]}

    async def all_supported(self, review_id: str, sentence_id: str) -> bool:
        checks = await self._checks(sentence_id)
        if not checks or any(c.status != "supported" for c in checks):
            return False
        rows = list(await self.session.execute(select(ReviewEvidence, ReviewEvidenceCheck).join(
            ReviewEvidenceCheck, ReviewEvidenceCheck.evidence_id == ReviewEvidence.id).where(
                ReviewEvidence.review_id == review_id, ReviewEvidence.validation_status == "valid",
                ReviewEvidenceCheck.check_id.in_([c.id for c in checks]))))
        supported = set()
        texts = {}
        for evidence, link in rows:
            if link.relation == "counter":
                return False
            if link.relation not in {"supporting", "context"}:
                continue
            if evidence.design_node_id not in texts:
                _, texts[evidence.design_node_id] = await self._target_text(review_id, evidence.design_node_id)
            text = texts[evidence.design_node_id]
            if (not 0 <= evidence.start_offset < evidence.end_offset <= len(text)
                    or text[evidence.start_offset:evidence.end_offset] != evidence.exact_quote):
                return False
            supported.add(link.check_id)
        return supported == {c.id for c in checks}

    async def ensure_complete(self, review_id: str, sentence_id: str) -> dict:
        result = await self.coverage(review_id, sentence_id)
        if result["can_stop_for_sufficient_evidence"]:
            return {**result, "termination_reason": "all_checks_supported",
                    "exhaustive": not result["mandatory_remaining_node_ids"] and not result["unfinished_passage_ids"]
                    and not result["blocked_passage_ids"]}
        if not result["checks_finalized"] or not result["passage_count"]:
            raise ValueError("complete check set and actual passage assessments are required")
        if result["mandatory_remaining_node_ids"] or result["unfinished_passage_ids"] or result["blocked_passage_ids"]:
            raise ValueError("unassessed mandatory ranges or unfinished passage tasks remain")
        return {**result, "termination_reason": "investigated_scope_completed", "exhaustive": True}

    async def conclude(self, review_id: str, sentence_id: str, conclusions: list[dict],
                       *, finalize: bool = True, review_pipeline: str = "full_investigation") -> dict:
        if review_pipeline not in {"fixed_tlr", "full_investigation"}:
            raise ValueError("unknown review pipeline")
        sentence = await self._locked_sentence(review_id, sentence_id)
        checks = {c.id: c for c in await self._checks(sentence_id)}
        if review_pipeline == "fixed_tlr":
            passages = list(await self.session.scalars(select(ReviewPassage).where(
                ReviewPassage.sentence_id == sentence_id,
                ReviewPassage.checkset_version == sentence.checkset_version)))
            if any(p.candidate_id is None for p in passages):
                raise ValueError("fixed TLR pipeline cannot adopt supplementary investigation facts")
        ids = [d["check_id"] for d in conclusions]
        if len(ids) != len(set(ids)) or not set(ids) <= checks.keys():
            raise ValueError("final conclusions contain duplicate or foreign checks")
        try:
            saved = await self.conclusions(sentence, list(checks.values()))
            ledger = {d["check_id"]: d for d in saved["conclusions"]}
            for d in conclusions:
                if d["status"] == "pending" or not d["note"].strip():
                    raise ValueError("final conclusion requires semantic status and reason")
                await self.update_check(review_id, d["check_id"], d["status"], d["note"], commit=False)
                ledger[d["check_id"]] = {"check_id": d["check_id"], "status": d["status"], "note": d["note"]}
            # A cumulative judgment may invalidate an earlier optimistic
            # passage result. Persist that correction before requiring more
            # investigation, rather than rolling it back into "supported".
            scope = (await self.ensure_complete(review_id, sentence_id) if finalize
                     else await self.coverage(review_id, sentence_id))
            if finalize and (set(ledger) != checks.keys() or any(c.status == "pending" for c in checks.values())):
                raise ValueError("final conclusion requires an explicit cumulative judgment for every check")
            scope.update(conclusions=list(ledger.values()),
                         remaining_conclusion_check_ids=sorted(checks.keys() - ledger.keys()),
                         conclusions_complete=bool(checks) and set(ledger) == checks.keys(),
                         review_pipeline=review_pipeline,
                         evidence_scope="fixed_tlr_material_only" if review_pipeline == "fixed_tlr" else "recorded_investigation_scope")
            if finalize:
                set_state(sentence, "sentence", "completed", reason="cumulative_conclusions_complete")
            elif sentence.status != "pending":
                set_state(sentence, "sentence", "pending", reason="cumulative_conclusions_partial")
            if not finalize:
                set_state(await self._review(review_id), "review", "running", reason="cumulative_facts_updated")
            task = await self.session.scalar(select(ReviewTask).where(
                ReviewTask.review_id == review_id, ReviewTask.task_key == f"analysis:{sentence_id}"))
            if task is None:
                task = ReviewTask(review_id=review_id, task_key=f"analysis:{sentence_id}", role="review_analysis")
                self.session.add(task)
            output = {"sentence_id": sentence_id, "conclusions": list(ledger.values()),
                      "review_pipeline": review_pipeline,
                      "material_fingerprint": saved["material_fingerprint"],
                      "completed": finalize, "statuses": {c.id: c.status for c in checks.values()},
                      "investigation_scope": scope,
                      "completion_kind": "workflow_finished_not_all_requirements_satisfied"}
            if task is not None:
                target_task_status = "completed" if finalize else "pending"
                set_state(task, "task", target_task_status,
                          reason="conclusions_finalized" if finalize else "conclusions_pending")
                task.output_json = output
                task.error_code = task.error_message = None
            if finalize:
                review = await self._review(review_id)
                all_sentences = list(await self.session.scalars(select(ReviewSentence).where(
                    ReviewSentence.review_id == review_id)))
                all_checks = list(await self.session.scalars(select(ReviewCheck).join(ReviewSentence).where(
                    ReviewSentence.review_id == review_id)))
                finished = all(s.status == "completed" for s in all_sentences)
                counts = {s: sum(c.status == s for c in all_checks) for s in
                          ("pending", "supported", "contradicted", "insufficient", "uncertain")}
                set_state(review, "review", "completed" if finished else "running",
                          reason="all_sentences_concluded" if finished else "sentence_concluded")
                review.summary_json = {"last_sentence_termination": scope, "provisional": not finished,
                                       "verification_point_count": len(all_checks), "status_counts": counts,
                                       "completed_workflow": finished,
                                       "semantic_result": "unresolved" if counts["pending"] or counts["insufficient"] or counts["uncertain"]
                                       else "contradicted" if counts["contradicted"] else "supported",
                                       "note": "deterministic aggregation of saved check judgments, not independent semantic review"}
            self.session.add(ReviewEvent(review_id=review_id, actor_role="review_analysis",
                                        event_type="investigation_concluded", payload_json=output))
            await self.session.commit()
            return output
        except Exception:
            await self.session.rollback()
            raise
