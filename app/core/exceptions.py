"""统一异常体系。

设计原则
--------
* 所有业务异常继承 :class:`AppError`，携带统一错误结构 ``code / msg / data``。
* 对外只暴露简洁安全的 ``msg``；排查所需的上下文放进 ``data`` 与日志。
* 错误码统一来自 :class:`~app.core.error_codes.ErrorCode`，避免散落的魔法数字。
"""

from __future__ import annotations

from typing import Any

from app.core.error_codes import ErrorCode


class AppError(Exception):
    """应用统一异常基类。

    既可接收 :class:`ErrorCode` 枚举，也兼容历史用法直接传 ``int`` 错误码::

        raise AppError(ErrorCode.PARAM_ERROR)
        raise AppError(ErrorCode.NOT_FOUND, "知识库不存在", data={"kb_id": kb_id})
    """

    def __init__(
        self,
        code: ErrorCode | int,
        message: str | None = None,
        *,
        status_code: int | None = None,
        data: Any = None,
    ) -> None:
        if isinstance(code, ErrorCode):
            self.error_code: ErrorCode | None = code
            self.code = code.code
            self.message = message or code.msg
            resolved_status = code.http_status if status_code is None else status_code
        else:  # 兼容直接传 int 的旧写法
            self.error_code = None
            self.code = code
            self.message = message or "error"
            resolved_status = 400 if status_code is None else status_code

        super().__init__(self.message)
        self.status_code = resolved_status
        self.data = data

    def to_response(self) -> dict[str, Any]:
        """转换为对外统一的 JSON 结构。"""
        return {"code": self.code, "msg": self.message, "data": self.data}


def raise_error(
    code: ErrorCode,
    message: str | None = None,
    *,
    data: Any = None,
    status_code: int | None = None,
) -> None:
    """快速抛错工具函数。

    用于一行代码即可抛出标准异常，例如::

        if kb is None:
            raise_error(ErrorCode.NOT_FOUND, "知识库不存在", data={"kb_id": kb_id})
    """
    raise AppError(code, message, status_code=status_code, data=data)


# --------------------------------------------------------------------------- #
# 领域异常：在错误码枚举之上提供语义化、可复用的具名异常
# --------------------------------------------------------------------------- #
class KnowledgeBaseNotFoundError(AppError):
    def __init__(self, *, kb_id: str | None = None) -> None:
        super().__init__(
            ErrorCode.NOT_FOUND,
            "知识库不存在",
            data={"kb_id": kb_id} if kb_id else None,
        )


class IndexingError(AppError):
    def __init__(self, reason: str, *, document_id: str) -> None:
        # 对外简洁提示，对内保留具体原因便于排查
        super().__init__(
            ErrorCode.INDEXING_ERROR,
            data={"document_id": document_id, "status": 2, "reason": reason},
        )


class EmbeddingProviderError(AppError):
    """大模型 / 向量化调用异常，默认归入 LLM 错误段。"""

    def __init__(
        self,
        reason: str,
        *,
        code: ErrorCode = ErrorCode.LLM_ERROR,
    ) -> None:
        super().__init__(code, data={"reason": reason})


class LLMProviderError(AppError):
    """通用大模型调用异常（含 JSON 解析失败），默认归入 LLM 错误段。

    由 :class:`~app.providers.llm.base.LLMProvider` 实现抛出，业务层可据此降级。
    """

    def __init__(
        self,
        reason: str,
        *,
        code: ErrorCode = ErrorCode.LLM_ERROR,
    ) -> None:
        super().__init__(code, data={"reason": reason})
