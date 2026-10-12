"""Initialize only an empty configured database; never repair an existing schema.

Baseline: https://alembic.sqlalchemy.org/en/latest/cookbook.html#building-an-up-to-date-database-from-scratch
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base

ROOT = Path(__file__).resolve().parents[1]


async def initialize(url) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            if tables:
                raise RuntimeError("Database is not empty; use alembic upgrade head instead")
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def main() -> None:
    url = make_url(get_settings().database_url)
    if url.drivername.startswith("sqlite") and url.database != ":memory:":
        Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(initialize(url))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.stamp(config, "head")
    print("Initialized configured empty database with the current schema")


if __name__ == "__main__":
    main()
