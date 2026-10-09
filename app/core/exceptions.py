from typing import Any

from app.core.error_codes import ErrorCode


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode | int,
        message: str | None = None,
        *,
        status_code: int | None = None,
        data: Any = None,
    ) -> None:
        if isinstance(code, ErrorCode):
            self.code = code.code
            self.message = message or code.msg
            self.status_code = code.http_status if status_code is None else status_code
        else:
            self.code = code
            self.message = message or "error"
            self.status_code = status_code or 400
        self.data = data
        super().__init__(self.message)

    def to_response(self) -> dict[str, Any]:
        return {"code": self.code, "msg": self.message, "data": self.data}


class LLMProviderError(AppError):
    def __init__(self, reason: str, *, code: ErrorCode = ErrorCode.LLM_ERROR) -> None:
        super().__init__(code, data={"reason": reason})
