from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_document_service
from app.schemas.common import ApiResponse
from app.schemas.document import DocumentUpload, DocumentUploaded
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=ApiResponse[DocumentUploaded])
async def upload_document(
    request: DocumentUpload,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> ApiResponse[DocumentUploaded]:
    result = await service.upload(request)
    return ApiResponse(data=result)

