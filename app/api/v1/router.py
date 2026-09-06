from fastapi import APIRouter

from app.api.v1.routes import documents, knowledge_bases, rag
from app.modules.tlr.catalog import router as catalog_router
from app.modules.tlr.planning import router as planning_router
from app.modules.tlr.router import router as tlr_router

api_router = APIRouter()
api_router.include_router(knowledge_bases.router)
api_router.include_router(documents.router)
api_router.include_router(rag.router)
api_router.include_router(tlr_router)
api_router.include_router(catalog_router)
api_router.include_router(planning_router)

