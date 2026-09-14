"""Read-only projection of historical v1 results; never rewrite collaborators' stored runs."""

from app.modules.consistency.judge import LABELS, error_record


def project_result(result):
    if result.get("schema_version") == 2:
        return result
    records = []
    mapping = {"support": "implemented", "direct_conflict": "conflict"}
    for old in result.get("findings", []):
        state = (
            mapping.get(old["kind"], "judgement_error")
            if old.get("grounded")
            else "judgement_error"
        )
        records.append(
            {
                "citations": old.get("citations", []),
                "parts": [
                    {
                        "unit_id": None,
                        "requirement_quote": old["requirement_quote"],
                        "implemented_behavior": old["reason"] if state == "implemented" else "",
                        "outcomes": [state],
                        "reason": old["reason"],
                        "grounded": old.get("grounded", False),
                        "scope_complete": False,
                        "missing_behavior": "",
                        "grounding_errors": old.get("grounding_errors", []),
                    }
                ],
            }
        )
    if not records or result.get("verdict") in {"inconclusive", "not_verifiable"}:
        quote = result.get("packet", {}).get("requirement", {}).get("text", "")[:4000]
        records.append(error_record(quote, "历史判断未完成或不能可靠判断；按新版四类口径展示"))
    statuses = {s for r in records for p in r["parts"] for s in p["outcomes"]}
    return {
        "schema_version": 2,
        "legacy_projection": True,
        "outcomes": [s for s in LABELS if s in statuses],
        "outcome_labels": [LABELS[s] for s in LABELS if s in statuses],
        "implementations": records,
        "packet": result.get("packet", {}),
        "analysis_errors": result.get("grounding_errors", []),
        "verification_status": "unverified",
        "verification_level": "static_model_review",
        "whole_requirement_implemented": result.get("verdict") == "supported",
        "judgement": {k: v for k, v in result.get("judgement", {}).items() if k != "findings"},
        **({"error": result["error"]} if result.get("error") else {}),
    }
