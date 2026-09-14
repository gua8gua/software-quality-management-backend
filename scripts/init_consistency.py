"""Add requirement-analysis and consistency tables to an existing SQLite TLR database."""

import asyncio

from sqlalchemy import inspect

from app.db.session import engine
from app.modules.consistency.models import ConsistencyReview, ConsistencyRun
from app.modules.requirements.models import RequirementAnalysis


async def main():
    if engine.dialect.name != "sqlite":
        raise SystemExit("For PostgreSQL use the existing Alembic workflow: alembic upgrade head")
    async with engine.begin() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if "tlr_runs" not in tables:
            raise SystemExit("Initialize the existing TLR module first")
        for model in (ConsistencyRun, ConsistencyReview, RequirementAnalysis):
            await connection.run_sync(
                lambda conn, table=model.__table__: table.create(conn, checkfirst=True)
            )
    await engine.dispose()
    print("Requirement and consistency tables ready; existing TLR tables/configuration unchanged")


if __name__ == "__main__":
    asyncio.run(main())
