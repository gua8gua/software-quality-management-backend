from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.middleware import RequestLoggingMiddleware
from app.api.v1.router import router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.modules.artifacts.router import router as library_router
from app.modules.information_access.review_router import router as review_router
from app.modules.information_access.router import router as information_router
from app.modules.model_config.console_router import router as console_router
from app.modules.model_config.router import router as model_config_router

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

app = FastAPI(title=settings.app_name, version="1.0.0")
app.add_middleware(RequestLoggingMiddleware)
app.include_router(router, prefix="/api/v1")
app.include_router(library_router, prefix="/api/v1")
app.include_router(information_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")
app.include_router(model_config_router, prefix="/api/v1")
app.include_router(console_router, prefix="/api/v1")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.exception_handler(ValueError)
async def bad_tool_input(_request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(AppError)
async def model_config_error(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())
