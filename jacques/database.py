from sqlalchemy import or_, select, text
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
        # Migrate existing databases: add disc_uuid to jobs if not present.
        result = await conn.execute(text("PRAGMA table_info(jobs)"))
        if "disc_uuid" not in {row[1] for row in result}:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN disc_uuid TEXT"))

        # Migrate existing databases: add episode_assignments/selected_title_id to jobs if not present.
        result = await conn.execute(text("PRAGMA table_info(jobs)"))
        existing_columns = {row[1] for row in result}
        if "episode_assignments" not in existing_columns:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN episode_assignments TEXT"))
        if "selected_title_id" not in existing_columns:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN selected_title_id INTEGER"))

    # One-time backfill: seed ripped_discs from existing COMPLETE jobs.
    # Safe to run on every startup — skips any disc_label that already has a row.
    from .models.job import Job, JobStatus
    from .models.ripped_disc import RippedDisc

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job).where(
                Job.status == JobStatus.COMPLETE,
                or_(Job.disc_label.is_not(None), Job.disc_uuid.is_not(None)),
            )
        )
        jobs = result.scalars().all()
        for job in jobs:
            if job.disc_uuid is not None:
                existing = await session.scalar(
                    select(RippedDisc)
                    .where(RippedDisc.disc_uuid == job.disc_uuid)
                    .limit(1)
                )
            else:
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
