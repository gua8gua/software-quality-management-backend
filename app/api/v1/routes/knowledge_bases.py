from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_knowledge_base_service
from app.schemas.common import ApiResponse
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseCreated
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.post(
    "/create",
    response_model=ApiResponse[KnowledgeBaseCreated],
)
async def create_knowledge_base(
    request: KnowledgeBaseCreate,
    service: Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)],
) -> ApiResponse[KnowledgeBaseCreated]:
    result = await service.create(request)
    return ApiResponse(data=result)
