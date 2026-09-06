from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response

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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    return HTMLResponse("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>软件质量管理后端</title><body>
    <h1>软件质量管理后端已启动</h1>
    <p>这里是 API 服务。项目资料库请打开前端开发服务器。</p>
    <p><a href="http://localhost:5173">打开项目资料库（默认端口 5173）</a></p>
    <p><a href="/docs">API 文档</a> ·
    <a href="/api/v1/tlr/projects?tenant_id=local">检查 local 工作空间项目</a></p>
    <p>服务启动不代表数据库连接已通过，请使用上述项目接口检查。</p>
    </body></html>""")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


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
    logger.exception("UNHANDLED_ERROR | type=%s | path=%s", type(exc).__name__, _request.url.path)
    return JSONResponse(
        status_code=ErrorCode.SERVER_ERROR.http_status,
        content={
            "code": ErrorCode.SERVER_ERROR.code,
            "msg": ErrorCode.SERVER_ERROR.msg,
            "data": None,
        },
    )
