from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.tlr.models import TlrArtifact, TlrDataset, TlrRun


class TlrRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def scoped(self, model, identifier, tenant_id, project_id):
        obj = await self.session.scalar(
            select(model).where(
                model.id == identifier, model.tenant_id == tenant_id, model.project_id == project_id
            )
        )
        if obj is None:
            raise AppError(ErrorCode.NOT_FOUND, "TLR 资源不存在")
        return obj

    async def dataset(self, identifier, tenant_id, project_id):
        return await self.scoped(TlrDataset, identifier, tenant_id, project_id)

    async def run(self, identifier, tenant_id, project_id):
        return await self.scoped(TlrRun, identifier, tenant_id, project_id)

    async def artifacts(self, dataset_id):
        return list(
            await self.session.scalars(
                select(TlrArtifact)
                .where(TlrArtifact.dataset_id == dataset_id)
                .order_by(TlrArtifact.external_id)
            )
        )

    async def rows(self, model, run_id):
        return list(await self.session.scalars(select(model).where(model.run_id == run_id)))

    async def page(self, model, column, identifier, schema, offset, limit):
        total = await self.session.scalar(
            select(func.count()).select_from(model).where(column == identifier)
        )
        rows = await self.session.scalars(
            select(model).where(column == identifier).order_by(model.id).offset(offset).limit(limit)
        )
        return {
            "items": [schema.model_validate(r).model_dump(mode="json") for r in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
