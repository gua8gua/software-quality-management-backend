"""Shared execution, validation, and result-validity semantics.

Execution failures are deliberately not represented as semantic outcomes. This
module is dependency-light so both TLR fine validation and requirement
evaluation can persist the same failure vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.core.error_codes import ErrorCode
from app.core.exceptions import LLMProviderError


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LEGACY = "legacy"


class ValidationStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    VALID = "valid"
    INVALID = "invalid"
    LEGACY = "legacy"


class ResultValidity(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    VALID = "valid"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    LEGACY = "legacy"


class ErrorCategory(StrEnum):
    TRANSPORT = "transport"
    INFRASTRUCTURE = "infrastructure"
    CONTEXT_OVERFLOW = "context_overflow"
    OUTPUT_TRUNCATION = "output_truncation"
    STRUCTURED_OUTPUT = "structured_output"
    EVIDENCE_VALIDATION = "evidence_validation"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ErrorDescriptor:
    category: ErrorCategory
    code: str
    message: str


def fingerprint_payload(*parts: Any) -> str:
    """Return a stable SHA-256 fingerprint for result-affecting inputs."""

    encoded = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_prompt(text: str) -> str:
    """Normalize only representation details before hashing prompt source.

    Semantic whitespace inside a line remains significant.  Line endings and
    accidental trailing whitespace do not, so deployments on Windows/Linux
    produce the same fingerprint for the same prompt.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def prompt_hash(text: str) -> str:
    return hashlib.sha256(normalize_prompt(text).encode("utf-8")).hexdigest()


def schema_hash(schema: dict[str, Any]) -> str:
    encoded = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def classify_exception(error: BaseException) -> ErrorDescriptor:
    """Classify an execution/validation exception without inventing a verdict."""

    if isinstance(error, LLMProviderError):
        code = getattr(error, "code", None)
        numeric = getattr(code, "code", code)
        if numeric == ErrorCode.LLM_TIMEOUT.code:
            category, stable = ErrorCategory.TRANSPORT, "llm_timeout"
        elif numeric == ErrorCode.LLM_RATE_LIMIT.code:
            category, stable = ErrorCategory.TRANSPORT, "llm_rate_limit"
        elif numeric == ErrorCode.LLM_TOKEN_LIMIT.code:
            category, stable = ErrorCategory.CONTEXT_OVERFLOW, "llm_context_overflow"
        elif numeric == ErrorCode.LLM_OUTPUT_TRUNCATED.code:
            category, stable = ErrorCategory.OUTPUT_TRUNCATION, "llm_output_truncated"
        elif numeric == ErrorCode.LLM_NO_RESPONSE.code:
            category, stable = ErrorCategory.STRUCTURED_OUTPUT, "llm_invalid_structured_output"
        elif numeric in {ErrorCode.LLM_MODEL_ERROR.code, ErrorCode.LLM_CONFIG_ERROR.code}:
            category, stable = ErrorCategory.INFRASTRUCTURE, "llm_unavailable"
        else:
            category, stable = ErrorCategory.TRANSPORT, "llm_provider_error"
        return ErrorDescriptor(category, stable, str(error))

    if isinstance(error, ValidationError):
        return ErrorDescriptor(
            ErrorCategory.STRUCTURED_OUTPUT, "schema_validation_failed", str(error)
        )

    if type(error).__name__ in {
        "LocalEmbeddingUnavailableError",
        "EmbeddingProviderError",
    }:
        return ErrorDescriptor(
            ErrorCategory.INFRASTRUCTURE,
            "embedding_unavailable",
            str(error),
        )

    diagnostic = getattr(error, "diagnostic", None)
    if diagnostic:
        return ErrorDescriptor(ErrorCategory.EVIDENCE_VALIDATION, str(diagnostic), str(error))

    return ErrorDescriptor(ErrorCategory.UNKNOWN, type(error).__name__, str(error))


def classify_legacy_fine_result(
    final_relation: str | None, diagnostics: dict[str, Any] | None
) -> dict[str, Any]:
    """Classify persisted pre-v2 Fine rows without calling a model again.

    The legacy semantic value is intentionally left untouched by migrations;
    these fields state whether it was actually produced and validated.
    """

    if final_relation is None:
        return {
            "execution_status": ExecutionStatus.PENDING,
            "validation_status": ValidationStatus.NOT_CHECKED,
            "error_category": None,
            "error_code": None,
            "attempt_count": 0,
        }
    codes = set((diagnostics or {}).get("validation") or [])
    structured = {"llm_invalid_structured_output"}
    evidence = {
        "evidence_quote_validation_failed",
        "evidence_node_outside_package",
        "unparsed_picture_used_as_text_evidence",
        "direct_target_evidence_missing",
        "direct_evidence_found_in_support_only",
    }
    if codes & structured:
        return {
            "execution_status": ExecutionStatus.FAILED,
            "validation_status": ValidationStatus.INVALID,
            "error_category": ErrorCategory.STRUCTURED_OUTPUT,
            "error_code": sorted(codes & structured)[0],
            "attempt_count": 2,
        }
    if codes & evidence:
        return {
            "execution_status": ExecutionStatus.FAILED,
            "validation_status": ValidationStatus.INVALID,
            "error_category": ErrorCategory.EVIDENCE_VALIDATION,
            "error_code": sorted(codes & evidence)[0],
            "attempt_count": 2,
        }
    return {
        "execution_status": ExecutionStatus.SUCCEEDED,
        "validation_status": ValidationStatus.VALID,
        "error_category": None,
        "error_code": None,
        "attempt_count": 1,
    }
