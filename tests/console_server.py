"""Isolated browser-test server; never use these deterministic providers in production."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parent))
from test_tlr import TestEmbedding, TestLLM

from app.api.dependencies import get_embedding_provider, get_llm_provider
from app.db.session import get_db
from app.main import app
from app.modules.tlr.models import TLR_TABLES

directory = Path(__file__).resolve().parents[1] / "artifacts/console-qa"
directory.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'browser-layers.sqlite'}")
factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def lifespan(_app):
    metadata = MetaData()
    for table in TLR_TABLES:
        table.to_metadata(metadata)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield
    await engine.dispose()


async def session():
    async with factory() as db:
        yield db


app.router.lifespan_context = lifespan
app.dependency_overrides[get_db] = session
app.dependency_overrides[get_embedding_provider] = TestEmbedding
app.dependency_overrides[get_llm_provider] = TestLLM
