"""Explicit write-state tools; none alter canonical content or formal TLR."""

from sqlalchemy import select

from app.modules.artifacts.models import ContentNode
from app.modules.information_access.review_models import (
    Review,
    ReviewBatch,
    ReviewCheck,
    ReviewEvidence,
    ReviewEvidenceCheck,
    ReviewSentence,
    ReviewTask,
    ReviewVisit,
)
from app.modules.information_access.service import InformationTools
from app.modules.information_access.state_machine import (
    require_check_status,
    set_state,
)
from app.modules.traceability.models import Candidate, TraceLink, TraceUnit
from app.modules.traceability.projections import node_content


class ReviewTools(InformationTools):
    _ACTIVE_REVIEW_STATUSES = {"open", "prepared", "running"}

    async def _mandatory_ids(self, review: Review) -> set[str]:
        result: set[str] = set()
        offset = 0
        while True:
            page = await self.mandatory_tlr_nodes(
                review.tlr_run_id, review.requirement_node_id, offset=offset, limit=100,
                target_artifact_ids=(review.scope_json or {}).get("target_artifact_ids"),
                target_node_ids=(review.scope_json or {}).get("target_node_ids"))
            result.update(item["node_id"] for item in page["items"])
            if not page["has_more"]:
                return result
            offset = page["next_offset"]

    async def _review(self, review_id: str) -> Review:
        row = await self.session.get(Review, review_id)
        if row is None or row.project_id != self.project_id:
            raise ValueError("review not found in project scope")
        await self.run(row.tlr_run_id)
        return row

    async def _sentence(self, review_id: str, sentence_id: str) -> ReviewSentence:
        await self._review(review_id)
        row = await self.session.get(ReviewSentence, sentence_id)
        if row is None or row.review_id != review_id:
            raise ValueError("sentence not found in review")
        return row

    async def _check(self, review_id: str, check_id: str) -> ReviewCheck:
        await self._review(review_id)
        row = await self.session.get(ReviewCheck, check_id)
        sentence = await self.session.get(ReviewSentence, row.sentence_id) if row else None
        if row is None or sentence is None or sentence.review_id != review_id:
            raise ValueError("check not found in review")
        return row

    async def create_review(self, tlr_run_id: str, requirement_node_id: str) -> dict:
        run = await self.run(tlr_run_id)
        if run.status != "completed":
            raise ValueError("formal TLR run is not completed")
        node = await self.node(requirement_node_id)
        source_ids = set((run.config or {}).get("source_artifact_ids", []))
        if node.artifact_id not in source_ids:
            raise ValueError("requirement is outside TLR source artifacts")
        row = Review(project_id=self.project_id, tlr_run_id=tlr_run_id,
                     requirement_node_id=requirement_node_id)
        self.session.add(row)
        await self.session.commit()
        return {"review_id": row.id, "status": row.status,
                "tlr_run_id": tlr_run_id, "requirement_node_id": requirement_node_id}

    async def create_reviews(self, tlr_run_id: str, source_artifact_ids: list[str],
                             target_artifact_ids: list[str], name: str = "需求—设计一致性分析",
                             description: str = "", source_node_ids: list[str] | None = None,
                             target_node_ids: list[str] | None = None) -> dict:
        """Create one durable review workspace for every TLR-supported source node.

        The UI may additionally submit canonical-node scope. Nodes without a
        candidate into the selected target scope are never scheduled. Parent
        selections are accepted and normalized to their participating
        descendants, so the persisted scope remains a list of real leaf/unit
        node IDs rather than a UI-only tree state.
        """
        run = await self.run(tlr_run_id)
        if run.status != "completed":
            raise ValueError("formal TLR run is not completed")
        configured_sources = set((run.config or {}).get("source_artifact_ids", []))
        configured_targets = set((run.config or {}).get("target_artifact_ids", []))
        selected_sources, selected_targets = set(source_artifact_ids), set(target_artifact_ids)
        if not selected_sources or not selected_targets:
            raise ValueError("source_artifact_ids and target_artifact_ids must not be empty")
        for artifact_id in selected_sources | selected_targets:
            artifact = await self.artifact(artifact_id)
            expected = "requirement" if artifact_id in selected_sources else "design"
            if artifact.modality != "text" or artifact.engineering_role != expected:
                raise ValueError("artifact does not match selected source/target document type")
        source_ids, target_ids = selected_sources & configured_sources, selected_targets & configured_targets
        if not source_ids or not target_ids:
            raise ValueError("所选工件没有被这条已完成追溯记录覆盖的源—目标范围")
        source_units_all = list(await self.session.scalars(select(TraceUnit).where(
            TraceUnit.run_id == run.id, TraceUnit.side == "source",
            TraceUnit.artifact_id.in_(source_ids)).order_by(TraceUnit.ordinal, TraceUnit.id)))
        target_units_all = list(await self.session.scalars(select(TraceUnit).where(
            TraceUnit.run_id == run.id, TraceUnit.side == "target",
            TraceUnit.artifact_id.in_(target_ids))))
        source_unit_by_id = {unit.id: unit for unit in source_units_all}
        target_unit_by_id = {unit.id: unit for unit in target_units_all}
        candidate_rows = list(await self.session.scalars(select(Candidate).where(
            Candidate.run_id == run.id)))
        used_source_unit_ids = {row.source_unit_id for row in candidate_rows
                                if row.source_unit_id in source_unit_by_id}
        used_target_unit_ids = {row.target_unit_id for row in candidate_rows
                                if row.target_unit_id in target_unit_by_id}
        # A formal link is also a TLR participation fact, even if an older
        # run has no retained candidate row for that link.
        for link in await self.session.scalars(select(TraceLink).where(TraceLink.run_id == run.id)):
            source_unit = next((u for u in source_units_all if u.node_id == link.source_node_id), None)
            target_unit = next((u for u in target_units_all if u.node_id == link.target_node_id), None)
            if source_unit:
                used_source_unit_ids.add(source_unit.id)
            if target_unit:
                used_target_unit_ids.add(target_unit.id)

        selected_node_ids = set(source_node_ids or []) | set(target_node_ids or [])
        selectable_nodes = list(await self.session.scalars(select(ContentNode).where(
            ContentNode.artifact_id.in_(source_ids | target_ids))))
        selectable_by_id = {node.id: node for node in selectable_nodes}
        unknown = selected_node_ids - set(selectable_by_id)
        if unknown:
            raise ValueError("selected node is outside selected TLR artifacts")

        def selected_by_parent(node: ContentNode, requested: set[str]) -> bool:
            current = node
            seen: set[str] = set()
            while current and current.id not in seen:
                if current.id in requested:
                    return True
                seen.add(current.id)
                current = selectable_by_id.get(current.parent_id)
            return False

        source_requested = set(source_node_ids or [])
        target_requested = set(target_node_ids or [])
        if source_requested and any(selectable_by_id[node_id].artifact_id not in source_ids
                                    for node_id in source_requested):
            raise ValueError("source node is outside selected source artifacts")
        if target_requested and any(selectable_by_id[node_id].artifact_id not in target_ids
                                    for node_id in target_requested):
            raise ValueError("target node is outside selected target artifacts")
        if source_requested:
            source_units = [u for u in source_units_all
                            if u.id in used_source_unit_ids
                            and selected_by_parent(selectable_by_id[u.node_id], source_requested)]
        else:
            source_units = [u for u in source_units_all if u.id in used_source_unit_ids]
        if target_requested:
            target_units = [u for u in target_units_all
                            if u.id in used_target_unit_ids
                            and selected_by_parent(selectable_by_id[u.node_id], target_requested)]
        else:
            target_units = [u for u in target_units_all if u.id in used_target_unit_ids]
        target_unit_ids = {unit.id for unit in target_units}
        source_unit_ids = {unit.id for unit in source_units}
        supported_source_unit_ids = set(await self.session.scalars(select(Candidate.source_unit_id).where(
            Candidate.run_id == run.id,
            Candidate.source_unit_id.in_(source_unit_ids),
            Candidate.target_unit_id.in_(target_unit_ids)))) if target_unit_ids else set()
        supported_node_ids = []
        for unit in source_units:
            if unit.id in supported_source_unit_ids and unit.node_id not in supported_node_ids:
                supported_node_ids.append(unit.node_id)
        node_rows = {node.id: node for node in await self.session.scalars(select(ContentNode).where(
            ContentNode.id.in_(supported_node_ids)))} if supported_node_ids else {}
        scope = {"source_artifact_ids": sorted(source_ids),
                 "target_artifact_ids": sorted(target_ids),
                 "selected_source_artifact_ids": sorted(selected_sources),
                 "selected_target_artifact_ids": sorted(selected_targets),
                 "excluded_artifact_ids": sorted((selected_sources - source_ids) | (selected_targets - target_ids)),
                 "selected_source_node_ids": sorted({unit.node_id for unit in source_units
                                                      if unit.id in supported_source_unit_ids}),
                 "selected_target_node_ids": sorted({unit.node_id for unit in target_units}),
                 "selection_mode": "node_scope" if source_requested or target_requested else "tlr_used_nodes"}
        scope["source_node_ids"] = scope["selected_source_node_ids"]
        scope["target_node_ids"] = scope["selected_target_node_ids"]
        batch = ReviewBatch(project_id=self.project_id, tlr_run_id=tlr_run_id,
                            name=name.strip(), description=description, scope_json=scope)
        self.session.add(batch)
        await self.session.flush()
        reviews = []
        skipped = 0
        for node_id in supported_node_ids:
            node = node_rows.get(node_id)
            if node is None or node.node_type not in {"paragraph", "table"}:
                skipped += 1
                continue
            row = Review(project_id=self.project_id, tlr_run_id=tlr_run_id,
                         requirement_node_id=node_id, scope_json=scope, batch_id=batch.id)
            self.session.add(row)
            reviews.append(row)
        if not reviews:
            raise ValueError("所选工件范围没有可用的追溯内容，未创建分析任务")
        batch.scope_json = {**scope, "source_node_count": len(reviews),
                            "unsupported_source_node_count": len(source_units) - len(supported_node_ids) + skipped}
        await self.session.commit()
        return {"batch_id": batch.id, "review_ids": [row.id for row in reviews],
                "source_node_count": len(reviews),
                "skipped_source_node_count": skipped,
                "tlr_run_id": tlr_run_id,
                "source_artifact_ids": sorted(source_ids),
                "target_artifact_ids": sorted(target_ids),
                "source_node_ids": scope["selected_source_node_ids"],
                "target_node_ids": scope["selected_target_node_ids"]}

    async def add_sentence(self, review_id: str, source_node_id: str,
                           start_offset: int, end_offset: int) -> dict:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES or source_node_id != review.requirement_node_id:
            raise ValueError("review closed or source node differs from requirement")
        node = await self.node(source_node_id)
        if node.node_type not in {"paragraph", "table"}:
            raise ValueError("source sentence requires textual canonical node")
        source_text = (node_content(node,
                                    {n.id: n for n in await self._nodes(node.artifact_id)},
                                    await self._relations(node.artifact_id))
                       if node.node_type == "table" else node.text)
        if not 0 <= start_offset < end_offset <= len(source_text):
            raise ValueError("sentence range outside canonical text")
        existing = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id).order_by(ReviewSentence.ordinal)))
        if any(start_offset < r.end_offset and end_offset > r.start_offset for r in existing):
            raise ValueError("sentence ranges overlap")
        row = ReviewSentence(review_id=review_id, source_node_id=source_node_id,
                             ordinal=len(existing) + 1, start_offset=start_offset,
                             end_offset=end_offset, text=source_text[start_offset:end_offset])
        self.session.add(row)
        await self.session.commit()
        return {"sentence_id": row.id, "ordinal": row.ordinal, "text": row.text,
                "source_node_id": source_node_id, "start_offset": start_offset,
                "end_offset": end_offset, "status": row.status}

    async def add_check(self, review_id: str, sentence_id: str, statement: str,
                        category: str = "function_result",
                        source_start: int | None = None,
                        source_end: int | None = None) -> dict:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is closed")
        sentence = await self._sentence(review_id, sentence_id)
        # Serialize check-set edits and passage commits on the same workspace row.
        sentence = await self.session.scalar(select(ReviewSentence).where(
            ReviewSentence.id == sentence.id).with_for_update())
        if sentence.status not in {"open", "points_ready", "no_points"} or not statement.strip() or len(statement) > 2000:
            raise ValueError("sentence closed or statement invalid")
        if category not in {"function_result", "condition_constraint"}:
            raise ValueError("invalid check category")
        if source_start is None or source_end is None:
            source_quote = ""
        else:
            if not sentence.start_offset <= source_start < source_end <= sentence.end_offset:
                raise ValueError("check source range outside sentence")
            source_quote = sentence.text[source_start - sentence.start_offset:source_end - sentence.start_offset]
        existing = list(await self.session.scalars(select(ReviewCheck).where(
            ReviewCheck.sentence_id == sentence_id)))
        for prior in existing:
            if (prior.statement == statement.strip() and prior.category == category
                    and prior.source_quote == source_quote):
                return {"check_id": prior.id, "sentence_id": sentence_id,
                        "statement": prior.statement, "category": prior.category,
                        "source_quote": prior.source_quote, "status": prior.status}
        if len(existing) >= 32:
            raise ValueError("review sentence has reached the verification-point limit")
        if sentence.checks_finalized:
            raise ValueError("verification-point set is finalized")
        row = ReviewCheck(sentence_id=sentence_id, ordinal=len(existing) + 1,
                          statement=statement.strip(), category=category,
                          source_quote=source_quote)
        self.session.add(row)
        sentence.checkset_version += 1
        await self.session.commit()
        return {"check_id": row.id, "sentence_id": sentence_id,
                "statement": row.statement, "category": row.category,
                "source_quote": row.source_quote, "status": row.status}

    async def record_evidence(self, review_id: str, check_id: str, design_node_id: str,
                              start_offset: int, end_offset: int, relation: str,
                              source: str = "agent_investigation",
                              candidate_id: str | None = None,
                              exact_quote: str | None = None, *, commit: bool = True) -> dict:
        check = await self._check(review_id, check_id)
        review = await self._review(review_id)
        await self.session.scalar(select(Review).where(Review.id == review_id).with_for_update())
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is closed")
        run = await self.run(review.tlr_run_id)
        node = await self.node(design_node_id)
        allowed_target_ids = set((review.scope_json or {}).get("target_artifact_ids", [])) or set(
            (run.config or {}).get("target_artifact_ids", []))
        if node.artifact_id not in allowed_target_ids:
            raise ValueError("evidence node outside TLR target artifacts")
        allowed_target_nodes = set((review.scope_json or {}).get("target_node_ids", []))
        if allowed_target_nodes and node.id not in allowed_target_nodes:
            raise ValueError("evidence node outside selected TLR node scope")
        evidence_text = (node_content(node,
                                      {n.id: n for n in await self._nodes(node.artifact_id)},
                                      await self._relations(node.artifact_id))
                         if node.node_type == "table" else node.text)
        if node.node_type == "picture" or not 0 <= start_offset < end_offset <= len(evidence_text):
            raise ValueError("no exact textual evidence at requested range")
        if exact_quote is not None and evidence_text[start_offset:end_offset] != exact_quote:
            raise ValueError("evidence quote is not exact canonical text at requested range")
        if relation not in {"supporting", "counter", "context"}:
            raise ValueError("invalid evidence relation")
        if candidate_id is not None:
            candidate = await self.session.get(Candidate, candidate_id)
            if candidate is None or candidate.run_id != run.id:
                raise ValueError("candidate outside review TLR run")
            source_unit = await self.session.get(TraceUnit, candidate.source_unit_id)
            target_unit = await self.session.get(TraceUnit, candidate.target_unit_id)
            if (source_unit is None or source_unit.node_id != review.requirement_node_id
                    or target_unit is None or target_unit.node_id != node.id):
                raise ValueError("candidate does not bind this source and evidence node")
        existing = await self.session.scalar(select(ReviewEvidence).where(
            ReviewEvidence.review_id == review.id,
            ReviewEvidence.design_node_id == node.id,
            ReviewEvidence.start_offset == start_offset,
            ReviewEvidence.end_offset == end_offset).order_by(ReviewEvidence.id).limit(1))
        row = existing or ReviewEvidence(review_id=review.id, design_node_id=node.id,
                             start_offset=start_offset, end_offset=end_offset,
                             exact_quote=evidence_text[start_offset:end_offset],
                             source=source[:32], candidate_id=candidate_id,
                             validation_status="valid")
        if existing is None:
            self.session.add(row)
            await self.session.flush()
        elif row.exact_quote != evidence_text[start_offset:end_offset]:
            raise ValueError("canonical content changed since evidence was recorded")
        link = await self.session.scalar(select(ReviewEvidenceCheck).where(
            ReviewEvidenceCheck.evidence_id == row.id, ReviewEvidenceCheck.check_id == check.id,
            ReviewEvidenceCheck.relation == relation))
        if link is None:
            self.session.add(ReviewEvidenceCheck(evidence_id=row.id, check_id=check.id, relation=relation))
        await self.session.flush()
        if commit:
            await self.session.commit()
        return {"evidence_id": row.id, "node_id": node.id,
                "exact_quote": row.exact_quote, "provenance": node.provenance_json,
                "start_offset": start_offset, "end_offset": end_offset,
                "relation": relation, "source": source, "candidate_id": candidate_id}

    async def update_check(self, review_id: str, check_id: str, status: str,
                           note: str = "", *, commit: bool = True) -> dict:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is closed")
        row = await self._check(review_id, check_id)
        if status not in {"pending", "supported", "contradicted", "insufficient", "uncertain"}:
            raise ValueError("invalid check status")
        evidence = list(await self.session.scalars(select(ReviewEvidenceCheck).join(
            ReviewEvidence, ReviewEvidence.id == ReviewEvidenceCheck.evidence_id).where(
            ReviewEvidenceCheck.check_id == check_id, ReviewEvidence.validation_status == "valid")))
        if status == "supported" and not any(e.relation in {"supporting", "context"} for e in evidence):
            raise ValueError("已满足状态需要有效的逐字原文证据，允许 supporting 或 context 证据关系。请保存对应引文后再提交检查点状态。")
        if status == "contradicted" and not any(e.relation == "counter" for e in evidence):
            raise ValueError("contradicted status requires exact counter evidence")
        if status in {"supported", "contradicted"} and {
            e.relation for e in evidence
        } >= {"supporting", "counter"}:
            raise ValueError("opposed evidence cannot receive a one-sided status")
        require_check_status(status)
        # A check may be refined as new canonical material is examined, but
        # execution errors are never represented by a semantic status here.
        row.status, row.note = status, note[:2000]
        await self.session.flush()
        if commit:
            await self.session.commit()
        return {"check_id": row.id, "status": row.status, "note": row.note}

    async def mark_mandatory_visited(self, review_id: str, design_node_id: str,
                                     note: str = "") -> dict:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is closed")
        if design_node_id not in await self._mandatory_ids(review):
            raise ValueError("node is not in mandatory TLR set")
        row = await self.session.scalar(select(ReviewVisit).where(
            ReviewVisit.review_id == review_id, ReviewVisit.design_node_id == design_node_id))
        if row is None:
            row = ReviewVisit(review_id=review_id, design_node_id=design_node_id)
            self.session.add(row)
        row.note = note[:2000]
        await self.session.commit()
        return {"review_id": review_id, "design_node_id": design_node_id, "visited": True}

    async def review_state(self, review_id: str, *, offset: int = 0,
                           limit: int = 50, mandatory_offset: int = 0,
                           mandatory_limit: int = 100) -> dict:
        self._page(offset, limit)
        self._page(mandatory_offset, mandatory_limit)
        review = await self._review(review_id)
        all_sentences = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id).order_by(ReviewSentence.ordinal)))
        sentences = all_sentences[offset:offset + limit]
        checks = list(await self.session.scalars(select(ReviewCheck).where(
            ReviewCheck.sentence_id.in_([s.id for s in sentences])))) if sentences else []
        visited = {r.design_node_id for r in await self.session.scalars(select(ReviewVisit).where(
            ReviewVisit.review_id == review_id))}
        mandatory = await self._mandatory_ids(review)
        mandatory_unvisited = sorted(mandatory - visited)
        mandatory_visited = sorted(mandatory & visited)
        return {"source": "review_business_state", "review_id": review.id,
                "status": review.status, "tlr_run_id": review.tlr_run_id,
                "requirement_node_id": review.requirement_node_id,
                "sentences": [{"sentence_id": s.id, "ordinal": s.ordinal,
                               "start_offset": s.start_offset, "end_offset": s.end_offset,
                               "text": s.text, "status": s.status,
                                           "checks": [{"check_id": c.id, "statement": c.statement,
                                           "ordinal": c.ordinal, "category": c.category,
                                           "source_quote": c.source_quote,
                                           "status": c.status, "note": c.note}
                                          for c in checks if c.sentence_id == s.id]}
                              for s in sentences],
                 "mandatory_unvisited_node_ids": mandatory_unvisited[mandatory_offset:mandatory_offset + mandatory_limit],
                 "mandatory_visited_node_ids": mandatory_visited[mandatory_offset:mandatory_offset + mandatory_limit],
                 "mandatory_total": len(mandatory),
                 "mandatory_unvisited_total": len(mandatory_unvisited),
                  "mandatory_has_more": (mandatory_offset + mandatory_limit < max(
                      len(mandatory_unvisited), len(mandatory_visited))),
                  "mandatory_next_offset": mandatory_offset + mandatory_limit if mandatory_offset + mandatory_limit < max(
                      len(mandatory_unvisited), len(mandatory_visited)) else None,
                 "sentence_total": len(all_sentences),
                 "has_more_sentences": offset + limit < len(all_sentences),
                  "next_offset": offset + limit if offset + limit < len(all_sentences) else None}

    async def complete_sentence(self, review_id: str, sentence_id: str) -> dict:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is closed")
        sentence = await self._sentence(review_id, sentence_id)
        from app.modules.information_access.passage_service import PassageService

        await PassageService(self.session, self.project_id, self.tenant_id).ensure_complete(
            review_id, sentence_id)
        checks = list(await self.session.scalars(select(ReviewCheck).where(
            ReviewCheck.sentence_id == sentence_id)))
        if not checks or any(c.status == "pending" for c in checks):
            raise ValueError("sentence has no checks or pending checks")
        set_state(sentence, "sentence", "completed", reason="explicit_check_conclusion")
        task = await self.session.scalar(select(ReviewTask).where(
            ReviewTask.review_id == review_id,
            ReviewTask.task_key == f"analysis:{sentence_id}"))
        if task is not None:
            set_state(task, "task", "completed", reason="explicit_check_conclusion")
            task.output_json = {"check_ids": [check.id for check in checks],
                                "statuses": {check.id: check.status for check in checks}}
        set_state(review, "review", "running", reason="facts_updated")
        await self.session.commit()
        return {"sentence_id": sentence_id, "status": sentence.status}

    async def complete_review(self, review_id: str) -> dict:
        review = await self._review(review_id)
        if review.status not in self._ACTIVE_REVIEW_STATUSES:
            raise ValueError("review is already closed")
        sentences = list(await self.session.scalars(select(ReviewSentence).where(
            ReviewSentence.review_id == review_id)))
        if not sentences or any(s.status != "completed" for s in sentences):
            raise ValueError("review sentences incomplete")
        from app.modules.information_access.passage_service import PassageService

        passage_service = PassageService(self.session, self.project_id, self.tenant_id)
        for sentence in sentences:
            await passage_service.ensure_complete(review_id, sentence.id)
        set_state(review, "review", "completed", reason="all_sentences_concluded")
        await self.session.commit()
        return await self.review_state(review_id)
