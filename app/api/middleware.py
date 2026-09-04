"""HTTP 请求 / 响应日志中间件。

相较装饰器，中间件能无侵入地拿到 URL、方法、原始请求体、状态码与耗时，是
FastAPI 体系下接口日志的推荐落地方式。

每条请求会：
1. 读取或生成 ``request_id``（支持上游透传 ``X-Request-Id``），写入协程上下文；
2. 记录 ``HTTP_REQUEST``（方法、URL、请求体，超长截断）；
3. 记录 ``HTTP_RESPONSE``（状态码、耗时），并把 ``request_id`` 回写响应头。
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import new_request_id, set_request_id
from app.core.logging import get_logger

logger = get_logger("app.http")

# 无需记录请求日志的路径前缀（探针 / 文档）
_SKIP_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")
_MAX_BODY_LOG = 4000


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        request_id = request.headers.get("X-Request-Id") or new_request_id()
        set_request_id(request_id)  # 后续所有日志自动携带该 rid

        body = await request.body()

        # 重新注入 receive，保证下游路由仍能读取请求体
        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # noqa: SLF001

        started = time.perf_counter()
        logger.info(
            "HTTP_REQUEST | method=%s | path=%s | query=%s | body=%s",
            request.method,
            request.url.path,
            request.url.query or "-",
            _preview_body(body),
        )

        try:
            response = await call_next(request)
        except Exception as exc:  # 记录后抛出，交由全局异常处理器统一返回
            cost = (time.perf_counter() - started) * 1000
            logger.exception(
                "HTTP_ERROR | method=%s | path=%s | cost=%.2fms | error=%s",
                request.method,
                request.url.path,
                cost,
                exc,
            )
            raise

        cost = (time.perf_counter() - started) * 1000
        response.headers["X-Request-Id"] = request_id
        level = "info" if response.status_code < 500 else "error"
        getattr(logger, level)(
            "HTTP_RESPONSE | method=%s | path=%s | status=%s | cost=%.2fms",
            request.method,
            request.url.path,
            response.status_code,
            cost,
        )
        return response


def _preview_body(body: bytes) -> str:
    if not body:
        return "-"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary {len(body)} bytes>"
    if len(text) > _MAX_BODY_LOG:
        return text[:_MAX_BODY_LOG] + f"...<truncated {len(text) - _MAX_BODY_LOG} chars>"
    return text
