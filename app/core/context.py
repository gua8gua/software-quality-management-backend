"""请求级上下文：跨协程安全地传递 ``request_id``。

基于 :mod:`contextvars`，在 asyncio 下每个请求/任务拥有独立副本，
日志过滤器据此把请求 ID 自动注入到每条日志，无需手动透传。
"""

from __future__ import annotations

import contextvars
import uuid

# 默认值 "-" 表示当前不在请求上下文中（如启动期、后台任务）
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def new_request_id() -> str:
    """生成一个短请求 ID（16 位十六进制）。"""
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return request_id_var.set(request_id)


def get_request_id() -> str:
    return request_id_var.get()
