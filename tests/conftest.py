import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from jacques.database import Base


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    """File-based SQLite session factory for integration tests.

    Deliberately not `:memory:` — SQLAlchemy forces a `:memory:` sqlite engine
    onto StaticPool (a single physical connection shared by every session),
    which is fine as long as DB access stays strictly sequential but silently
    corrupts state once two sessions are genuinely concurrent (e.g. the
    pipeline's metadata-fetch task racing the rip loop). A temp file gives a
    real connection pool, matching how the production engine (database.py)
    talks to its file-based db.
    """
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()
