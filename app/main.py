from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.middleware import RequestLoggingMiddleware
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(
    settings.log_level,
    log_dir=settings.log_dir,
    file_enabled=settings.log_to_file,
    rotation=settings.log_rotation,
    max_bytes=settings.log_max_bytes,
    backup_count=settings.log_backup_count,
    use_color=settings.log_use_color,
)
logger = get_logger(__name__)

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.include_router(api_router, prefix="/api/v1")


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    # 对内日志：完整、带上下文（data），方便排查；对外只返回简洁 msg
    logger.warning(
        "APP_ERROR | code=%s | msg=%s | status=%s | data=%s",
        exc.code,
        exc.message,
        exc.status_code,
        exc.data,
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("VALIDATION_ERROR | errors=%s", jsonable_encoder(exc.errors()))
    return JSONResponse(
        status_code=ErrorCode.PARAM_ERROR.http_status,
        content=jsonable_encoder(
            {
                "code": ErrorCode.PARAM_ERROR.code,
                "msg": ErrorCode.PARAM_ERROR.msg,
                "data": exc.errors(),
            }
        ),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    # 完整堆栈 + 异常类型 + 上下文
    logger.exception(
        "UNHANDLED_ERROR | type=%s | path=%s", type(exc).__name__, _request.url.path
    )
    return JSONResponse(
        status_code=ErrorCode.SERVER_ERROR.http_status,
        content={
            "code": ErrorCode.SERVER_ERROR.code,
            "msg": ErrorCode.SERVER_ERROR.msg,
            "data": None,
        },
    )
