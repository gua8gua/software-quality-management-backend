from sqlalchemy.ext.asyncio import AsyncSession

from app.models.retrieval_log import RetrievalLog


class RetrievalLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, retrieval_log: RetrievalLog) -> RetrievalLog:
        self._session.add(retrieval_log)
        await self._session.flush()
        return retrieval_log

