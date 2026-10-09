"""A completed investigation requires explicit, current cumulative conclusions.

Passage judgments are not final check judgments. This ledger is a business
checkpoint in ReviewTask, not a second Agent loop or a generated fallback.
"""
import hashlib
import json


def material_fingerprint(sentence, checks, passages, assessments) -> str:
    value = {
        "source": [sentence.source_node_id, sentence.start_offset, sentence.end_offset,
                   sentence.text, sentence.checkset_version, sentence.checks_finalized],
        "checks": sorted((c.id, c.category, c.statement, c.source_quote) for c in checks),
        "passages": sorted((p.id, p.design_node_id, p.start_offset, p.end_offset,
                            p.candidate_id, p.content_fingerprint, p.checkset_version, p.status)
                           for p in passages),
        "assessments": sorted((a.passage_id, a.check_id, a.relation, a.note,
                               a.resulting_status, sorted(a.evidence_ids)) for a in assessments),
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def conclusion_state(task, sentence, checks, passages, assessments) -> dict:
    fingerprint = material_fingerprint(sentence, checks, passages, assessments)
    output = (task.output_json or {}) if task else {}
    rows = output.get("conclusions", []) if output.get("material_fingerprint") == fingerprint else []
    # Duplicate, stale and partial ledger rows never establish completion.
    by_id = {r["check_id"]: r for r in rows}
    valid = {c.id for c in checks if c.id in by_id
             and by_id[c.id].get("status") == c.status != "pending"
             and by_id[c.id].get("note") == c.note and c.note.strip()}
    valid = valid if len(by_id) == len(rows) else set()
    remaining = sorted({c.id for c in checks} - valid)
    return {"material_fingerprint": fingerprint,
            "review_pipeline": output.get("review_pipeline", "full_investigation"),
            "evidence_scope": (output.get("investigation_scope") or {}).get("evidence_scope"),
            "conclusions": [by_id[key] for key in sorted(valid)],
            "remaining_conclusion_check_ids": remaining,
            "conclusions_complete": bool(checks) and not remaining}
