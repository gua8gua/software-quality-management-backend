from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_requirement_extraction_service
from app.schemas.common import ApiResponse
from app.schemas.requirement import (
    RequirementExtractionRequest,
    RequirementExtractionResult,
)
from app.services.requirement_extraction_service import RequirementExtractionService

router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.post(
    "/extract",
    response_model=ApiResponse[RequirementExtractionResult],
)
async def extract_requirements(
    request: RequirementExtractionRequest,
    service: Annotated[RequirementExtractionService, Depends(get_requirement_extraction_service)],
) -> ApiResponse[RequirementExtractionResult]:
    result = await service.extract(
        document_id=request.document_id,
        content=request.content,
        source_location=request.source_location,
    )
    return ApiResponse(data=result)
