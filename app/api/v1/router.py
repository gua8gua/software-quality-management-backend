from fastapi import APIRouter

from app.api.v1.routes import documents, knowledge_bases, rag, requirements

api_router = APIRouter()
api_router.include_router(knowledge_bases.router)
api_router.include_router(documents.router)
api_router.include_router(rag.router)
api_router.include_router(requirements.router)
