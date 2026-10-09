import pytest

from app.modules.information_access.state_machine import (
    InvalidStateTransition,
    assert_error_not_semantic,
    execution_failure,
    transition,
)


def test_same_state_is_idempotent_and_completed_review_reopen_is_explicit():
    assert transition("review", "running", "running") == "running"
    assert transition("review", "completed", "prepared", reason="new_attempt") == "prepared"
    with pytest.raises(InvalidStateTransition):
        transition("review", "completed", "prepared", reason="implicit_retry")


def test_terminal_passage_cannot_be_replaced_by_execution_failure():
    with pytest.raises(InvalidStateTransition):
        transition("passage", "completed", "blocked", reason="provider_timeout")


def test_execution_failure_is_not_a_semantic_check_result():
    failure = execution_failure("context_budget_admission_blocked", "too large", stage="analysis")
    assert failure["execution_status"] == "blocked"
    assert failure["semantic_status"] is None
    assert failure["semantic_result_available"] is False
    with pytest.raises(ValueError):
        assert_error_not_semantic({**failure, "semantic_status": "uncertain"})


def test_batch_does_not_accept_unknown_execution_state():
    with pytest.raises(InvalidStateTransition):
        transition("batch", "running", "unknown")
