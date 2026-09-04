from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_rag_service
from app.schemas.common import ApiResponse
from app.schemas.rag import RagRetrieveRequest, RagRetrieveResult
from app.services.rag_service import RagService

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/retrieve",
    response_model=ApiResponse[RagRetrieveResult],
    response_model_exclude_none=True,
)
async def retrieve(
    request: RagRetrieveRequest,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> ApiResponse[RagRetrieveResult]:
    result = await service.retrieve(request)
    return ApiResponse(data=result)

