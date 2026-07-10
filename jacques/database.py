from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
)

AsyncSessionLocal: sessionmaker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # One-time backfill: seed ripped_discs from existing COMPLETE jobs.
    # Safe to run on every startup — skips any disc_label that already has a row.
    from .models.job import Job, JobStatus
    from .models.ripped_disc import RippedDisc

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job).where(
                Job.status == JobStatus.COMPLETE,
                Job.disc_label.is_not(None),
            )
        )
        jobs = result.scalars().all()
        for job in jobs:
            existing = await session.scalar(
                select(RippedDisc)
                .where(RippedDisc.disc_label == job.disc_label)
                .limit(1)
            )
            if existing is None:
                session.add(
                    RippedDisc(
                        disc_label=job.disc_label,
                        disc_uuid=job.disc_uuid,
                        job_id=job.id,
                        ripped_at=job.updated_at,
                    )
                )
        await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
