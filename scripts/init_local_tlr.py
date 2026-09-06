"""Initialize an explicit SQLite TLR database. Never replace existing configuration."""

import asyncio
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.tlr.models import TLR_TABLES

ROOT = Path(__file__).resolve().parents[1]


async def main():
    config = ROOT / ".env"
    if config.exists():
        raise SystemExit(".env already exists; keep it and use its database migration workflow.")
    database = ROOT / "data/tlr.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    engine = create_async_engine(url)
    metadata = MetaData()
    for table in TLR_TABLES:
        table.to_metadata(metadata)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    await engine.dispose()
    with config.open("x", encoding="utf-8") as stream:
        stream.write(
            "# Local TLR-only database; legacy pgvector RAG needs PostgreSQL.\n"
            f"DATABASE_URL={url}\n"
        )
    print("Created local TLR configuration and tables:", database)


if __name__ == "__main__":
    asyncio.run(main())
