from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url)

# A few historical SQLite migrations used PostgreSQL's ``now()`` expression
# as a server default.  Production uses PostgreSQL, but local API/fixture
# databases must remain runnable as well.  Register the equivalent SQLite
# scalar only for SQLite connections; PostgreSQL is untouched.
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_now(dbapi_connection, _connection_record):
        dbapi_connection.create_function(
            "now", 0, lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        )


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
