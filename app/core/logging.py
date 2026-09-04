"""生产级日志系统。

特性
----
* 统一格式：``时间 | 级别 | 模块:函数:行号 | rid=请求ID | 事件 | 内容``
* 控制台彩色输出 + 文件按大小 / 时间滚动（app.log 全量、error.log 仅 ERROR+）
* 异步安全：所有 handler 经 ``QueueHandler`` 异步落盘，不阻塞业务事件循环
* 低侵入：``get_logger`` 全局可用；``api_log`` 装饰器、``log_llm_*`` 专用函数

使用::

    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("普通日志，自动带 request_id")
"""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
import sys
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from app.core.context import get_request_id

# --------------------------------------------------------------------------- #
# 格式与颜色
# --------------------------------------------------------------------------- #
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d "
    "| rid=%(rid)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # cyan
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}


class RequestContextFilter(logging.Filter):
    """把当前协程的 request_id 注入到每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = get_request_id()
        return True


class ColorFormatter(logging.Formatter):
    """控制台彩色格式器：仅对级别名上色，避免整行染色干扰阅读。"""

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno)
        original = record.levelname
        if color:
            record.levelname = f"{color}{original}{_RESET}"
        try:
            return super().format(record)
        finally:  # 复原，避免影响同一 record 被其它 handler 复用
            record.levelname = original


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
_CONFIGURED = False
_listener: logging.handlers.QueueListener | None = None


def _build_file_handler(
    log_dir: Path,
    *,
    rotation: str,
    max_bytes: int,
    backup_count: int,
    level: int,
    filename: str,
) -> logging.Handler:
    path = log_dir / filename
    if rotation == "time":
        handler: logging.Handler = logging.handlers.TimedRotatingFileHandler(
            path, when="midnight", backupCount=backup_count, encoding="utf-8"
        )
    else:  # size
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    return handler


def configure_logging(
    level: str = "INFO",
    *,
    log_dir: str = "logs",
    file_enabled: bool = True,
    rotation: str = "size",  # "size" | "time"
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
    use_color: bool | None = None,
) -> None:
    """初始化全局日志。可重复调用（会先停掉旧的后台线程再重建）。"""
    global _CONFIGURED, _listener

    if _listener is not None:  # 重建前先优雅停止，避免线程泄漏
        _listener.stop()
        _listener = None

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()  # 移除 basicConfig / 重复 handler

    context_filter = RequestContextFilter()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level.upper())
    if use_color is None:
        use_color = sys.stdout.isatty()
    formatter: logging.Formatter = (
        ColorFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        if use_color
        else logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    )
    console.setFormatter(formatter)

    handlers: list[logging.Handler] = [console]

    # 文件：app.log 全量 + error.log 仅 ERROR+
    if file_enabled:
        dir_path = Path(log_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        app_handler = _build_file_handler(
            dir_path,
            rotation=rotation,
            max_bytes=max_bytes,
            backup_count=backup_count,
            level=logging.DEBUG,
            filename="app.log",
        )
        error_handler = _build_file_handler(
            dir_path,
            rotation="size",  # 错误日志固定按大小滚动，避免单文件过大
            max_bytes=max_bytes,
            backup_count=backup_count,
            level=logging.ERROR,
            filename="error.log",
        )
        handlers.extend([app_handler, error_handler])

    # 异步落盘：业务线程只往队列放记录，后台线程统一写盘，不阻塞事件循环。
    # request_id 过滤器必须挂在入队前的 QueueHandler 上——此时仍在调用方协程
    # 上下文中，才能正确捕获 rid；挂在后台落盘 handler 上只会拿到默认值。
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_handler.addFilter(context_filter)
    root.addHandler(queue_handler)

    _listener = logging.handlers.QueueListener(log_queue, *handlers, respect_handler_level=True)
    _listener.start()
    atexit.register(_shutdown_logging)

    _CONFIGURED = True


def _shutdown_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


def get_logger(name: str | None = None) -> logging.Logger:
    """获取带统一配置的 logger，全局复用。"""
    if not _CONFIGURED:  # 兜底：未显式初始化也能用
        configure_logging()
    return logging.getLogger(name or "app")


# --------------------------------------------------------------------------- #
# 结构化工具
# --------------------------------------------------------------------------- #
_MAX_FIELD_LEN = 2000


def _truncate(value: Any, limit: int = _MAX_FIELD_LEN) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) > limit:
        return text[:limit] + f"...<truncated {len(text) - limit} chars>"
    return text


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    stacklevel: int = 2,
    **fields: Any,
) -> None:
    """输出业务关键节点日志：``EVENT | k1=v1 | k2=v2``。

    ``stacklevel`` 用于让日志定位到真正的业务调用处，而非本函数内部。
    """
    parts = [f"{key}={_truncate(value)}" for key, value in fields.items()]
    message = event if not parts else f"{event} | " + " | ".join(parts)
    logger.log(level, message, stacklevel=stacklevel)


# --------------------------------------------------------------------------- #
# 接口请求日志装饰器
# --------------------------------------------------------------------------- #
F = TypeVar("F", bound=Callable[..., Any])


def api_log(
    *,
    event_prefix: str = "API",
    log_args: bool = True,
    log_result: bool = True,
) -> Callable[[F], F]:
    """函数级请求日志装饰器（同步 / 异步通用）。

    输出示例::

        ... | INFO | ... | API_REQUEST | call_api | args=() | kwargs={}
        ... | INFO | ... | API_RESPONSE | call_api | cost=0.01ms | result={...}
    """

    def decorator(func: F) -> F:
        logger = get_logger(func.__module__)
        name = func.__name__

        def _request_message(args: tuple, kwargs: dict) -> str:
            if log_args:
                return (
                    f"{event_prefix}_REQUEST | {name} "
                    f"| args={_truncate(args)} | kwargs={_truncate(kwargs)}"
                )
            return f"{event_prefix}_REQUEST | {name}"

        if _is_async(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                logger.info(_request_message(args, kwargs), stacklevel=2)
                started = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:  # 记录后原样抛出，交给全局处理
                    cost = (time.perf_counter() - started) * 1000
                    logger.exception(
                        f"{event_prefix}_ERROR | {name} | cost={cost:.2f}ms | error={exc}",
                        stacklevel=2,
                    )
                    raise
                cost = (time.perf_counter() - started) * 1000
                if log_result:
                    logger.info(
                        f"{event_prefix}_RESPONSE | {name} | cost={cost:.2f}ms "
                        f"| result={_truncate(result)}",
                        stacklevel=2,
                    )
                else:
                    logger.info(
                        f"{event_prefix}_RESPONSE | {name} | cost={cost:.2f}ms", stacklevel=2
                    )
                return result

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.info(_request_message(args, kwargs), stacklevel=2)
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                cost = (time.perf_counter() - started) * 1000
                logger.exception(
                    f"{event_prefix}_ERROR | {name} | cost={cost:.2f}ms | error={exc}",
                    stacklevel=2,
                )
                raise
            cost = (time.perf_counter() - started) * 1000
            if log_result:
                logger.info(
                    f"{event_prefix}_RESPONSE | {name} | cost={cost:.2f}ms "
                    f"| result={_truncate(result)}",
                    stacklevel=2,
                )
            else:
                logger.info(
                    f"{event_prefix}_RESPONSE | {name} | cost={cost:.2f}ms", stacklevel=2
                )
            return result

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def _is_async(func: Callable[..., Any]) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)


# --------------------------------------------------------------------------- #
# 大模型交互专用日志
# --------------------------------------------------------------------------- #
_llm_logger: logging.Logger | None = None


def _get_llm_logger() -> logging.Logger:
    global _llm_logger
    if _llm_logger is None:
        _llm_logger = get_logger("app.llm")
    return _llm_logger


def log_llm_request(model: str, prompt: Any, *, _stacklevel: int = 3, **extra: Any) -> None:
    """记录大模型调用请求。"""
    log_event(
        _get_llm_logger(),
        logging.INFO,
        "LLM_REQUEST",
        stacklevel=_stacklevel,
        model=model,
        prompt=prompt,
        **extra,
    )


def log_llm_response(
    model: str, response: Any, cost_ms: float, *, _stacklevel: int = 3, **extra: Any
) -> None:
    """记录大模型调用返回。"""
    log_event(
        _get_llm_logger(),
        logging.INFO,
        "LLM_RESPONSE",
        stacklevel=_stacklevel,
        model=model,
        cost=f"{cost_ms:.2f}ms",
        response=response,
        **extra,
    )


def log_llm_error(
    model: str, error: Any, cost_ms: float | None = None, *, _stacklevel: int = 3, **extra: Any
) -> None:
    """记录大模型调用异常。"""
    fields: dict[str, Any] = {"model": model, "error": _truncate(error)}
    if cost_ms is not None:
        fields["cost"] = f"{cost_ms:.2f}ms"
    fields.update(extra)
    log_event(_get_llm_logger(), logging.ERROR, "LLM_ERROR", stacklevel=_stacklevel, **fields)


def llm_log(model: str) -> Callable[[F], F]:
    """大模型调用装饰器：自动记录 prompt / response / 耗时 / 异常。

    被装饰函数的第一个位置参数被视为 prompt。
    """

    def decorator(func: F) -> F:
        name = func.__name__

        if _is_async(func):

            @wraps(func)
            async def async_wrapper(prompt: Any, *args: Any, **kwargs: Any) -> Any:
                log_llm_request(model, prompt, _stacklevel=4, func=name)
                started = time.perf_counter()
                try:
                    result = await func(prompt, *args, **kwargs)
                except Exception as exc:
                    log_llm_error(
                        model, exc, (time.perf_counter() - started) * 1000, _stacklevel=4, func=name
                    )
                    raise
                log_llm_response(
                    model, result, (time.perf_counter() - started) * 1000, _stacklevel=4, func=name
                )
                return result

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(prompt: Any, *args: Any, **kwargs: Any) -> Any:
            log_llm_request(model, prompt, _stacklevel=4, func=name)
            started = time.perf_counter()
            try:
                result = func(prompt, *args, **kwargs)
            except Exception as exc:
                log_llm_error(
                    model, exc, (time.perf_counter() - started) * 1000, _stacklevel=4, func=name
                )
                raise
            log_llm_response(
                model, result, (time.perf_counter() - started) * 1000, _stacklevel=4, func=name
            )
            return result

        return sync_wrapper  # type: ignore[return-value]

    return decorator
