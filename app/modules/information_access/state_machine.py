"""Explicit state and error boundaries for durable consistency reviews.

The model may propose a semantic result or a tool action, but it never chooses
the lifecycle state.  This module is deliberately pure so every API route,
worker and recovery path applies the same rules.
"""

from __future__ import annotations


class InvalidStateTransition(ValueError):
    """A persisted object cannot move directly between these lifecycle states."""

    def __init__(self, entity: str, current: str, target: str, reason: str = ""):
        self.entity, self.current, self.target, self.reason = entity, current, target, reason
        suffix = f" ({reason})" if reason else ""
        super().__init__(f"invalid_{entity}_state_transition:{current}->{target}{suffix}")


REVIEW_TRANSITIONS = {
    "open": {"prepared", "running", "blocked"},
    "prepared": {"running", "completed", "blocked"},
    "running": {"prepared", "completed", "blocked"},
    "blocked": {"open", "prepared", "running"},
    # Reopening a completed review is only used by an explicit new execution
    # attempt; callers pass reason="new_attempt".
    "completed": {"prepared"},
}

BATCH_TRANSITIONS = {
    "pending": {"running", "interrupted"},
    "running": {"completed", "partial", "interrupted"},
    "partial": {"running", "completed", "interrupted"},
    "interrupted": {"running", "partial", "completed"},
    "completed": {"running"},
}

SENTENCE_TRANSITIONS = {
    "open": {"points_ready", "no_points", "pending", "completed"},
    "points_ready": {"pending", "completed", "open"},
    "no_points": {"pending", "completed", "open"},
    "pending": {"open", "completed"},
    "completed": {"pending", "open"},
}

PASSAGE_TRANSITIONS = {
    "pending": {"completed", "blocked"},
    "blocked": {"pending"},
    "completed": set(),
}

TASK_TRANSITIONS = {
    "pending": {"running", "completed", "blocked", "failed", "delegated",
                 "pending_validation", "revision_required"},
    "running": {"completed", "blocked", "failed", "delegated",
                 "pending_validation", "revision_required"},
    "blocked": {"pending", "running", "delegated"},
    "failed": {"pending", "running", "delegated"},
    "pending_validation": {"completed", "revision_required", "blocked", "pending"},
    "revision_required": {"pending", "running", "completed", "blocked"},
    "delegated": {"pending", "running", "completed", "blocked", "failed"},
    # A durable worker may explicitly start a new execution attempt after a
    # prior attempt was committed.  The caller must still supply the
    # ``new_attempt`` reason; this is not an implicit rewind.
    "completed": {"pending", "pending_validation", "revision_required", "blocked"},
}

CHECK_STATUSES = {"pending", "supported", "contradicted", "insufficient", "uncertain"}
SEMANTIC_CHECK_STATUSES = CHECK_STATUSES - {"pending"}
EXECUTION_STATUSES = {"pending", "running", "completed", "idle", "partial",
                      "blocked", "failed", "interrupted"}
DEFAULT_STATES = {"review": "open", "batch": "pending", "sentence": "open",
                  "passage": "pending", "task": "pending"}


def transition(entity: str, current: str, target: str, *, reason: str = "") -> str:
    """Validate and return a lifecycle target; same-state writes are idempotent."""
    tables = {
        "review": REVIEW_TRANSITIONS,
        "batch": BATCH_TRANSITIONS,
        "sentence": SENTENCE_TRANSITIONS,
        "passage": PASSAGE_TRANSITIONS,
        "task": TASK_TRANSITIONS,
    }
    try:
        allowed = tables[entity][current]
    except KeyError as exc:
        raise InvalidStateTransition(entity, current, target, reason or "unknown_state") from exc
    if current != target and target not in allowed:
        raise InvalidStateTransition(entity, current, target, reason)
    if (entity == "review" and current == "completed" and target == "prepared"
            and reason not in {"new_attempt", "validation_failure"}):
        raise InvalidStateTransition(entity, current, target, "completed_review_requires_new_attempt")
    return target


def set_state(row, entity: str, target: str, *, reason: str = "") -> str:
    """Apply one checked state mutation to an ORM row."""
    # SQLAlchemy column defaults are applied at INSERT/flush time.  A freshly
    # constructed row therefore has ``status is None`` in the same transaction
    # that first advances it.  Resolve that ORM-only absence to the explicit
    # lifecycle initial state; never persist ``None`` as a legal state.
    current = str(row.status or DEFAULT_STATES[entity])
    row.status = transition(entity, current, target, reason=reason)
    return row.status


def require_execution_status(status: str) -> str:
    if status not in EXECUTION_STATUSES:
        raise ValueError(f"invalid_execution_status:{status}")
    return status


def require_check_status(status: str) -> str:
    if status not in CHECK_STATUSES:
        raise ValueError(f"invalid_check_status:{status}")
    return status


def execution_failure(code: str, message: str, *, stage: str = "") -> dict:
    """Canonical shape for model/tool/runtime failure; never a semantic verdict."""
    return {"execution_status": "blocked", "error_code": str(code)[:128],
            "error_message": str(message)[:2000], "stage": stage,
            "semantic_status": None, "semantic_result_available": False}


def assert_error_not_semantic(payload: dict) -> None:
    """Guard against relabeling a failed execution as insufficient/uncertain."""
    if payload.get("error_code") and payload.get("semantic_status") in SEMANTIC_CHECK_STATUSES:
        raise ValueError("execution_error_cannot_have_semantic_status")
