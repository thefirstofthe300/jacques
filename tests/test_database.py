"""Tests for the init_db() backfill step.

The backfill queries all COMPLETE jobs with a non-null disc_label and inserts a
RippedDisc row for each disc_label that doesn't already have one.  It runs inside
init_db() on every startup, so it must be idempotent.

Strategy: each test builds its own in-memory engine, seeds Job rows directly,
then calls init_db() with the module-level `engine` and `AsyncSessionLocal`
patched to point at that same engine.  After the call, we inspect ripped_discs
using the same session factory.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from jacques.database import Base, init_db
from jacques.models.job import Job, JobStatus
from jacques.models.ripped_disc import RippedDisc


# ── fixture ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def backfill_db():
    """Fresh in-memory engine with schema created; yields (engine, factory).

    Tests seed data then call `_run_init_db(factory)` to exercise the backfill.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def _run_init_db(engine, factory) -> None:
    """Invoke init_db() with module-level objects patched to the test engine."""
    with (
        patch("jacques.database.engine", engine),
        patch("jacques.database.AsyncSessionLocal", factory),
    ):
        await init_db()


async def _count_ripped_discs(factory) -> int:
    async with factory() as session:
        result = await session.execute(select(RippedDisc))
        return len(result.scalars().all())


async def _get_ripped_disc(factory, disc_label: str) -> RippedDisc | None:
    async with factory() as session:
        return await session.scalar(
            select(RippedDisc).where(RippedDisc.disc_label == disc_label)
        )


# ── helpers ───────────────────────────────────────────────────────────────────

_UPDATED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _seed_job(
    factory,
    *,
    disc_label: str | None,
    disc_uuid: str | None = None,
    status: JobStatus = JobStatus.COMPLETE,
) -> Job:
    async with factory() as session:
        job = Job(
            drive_path="/dev/sr0",
            disc_label=disc_label,
            disc_uuid=disc_uuid,
            status=status,
            updated_at=_UPDATED_AT,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_complete_job_with_label_creates_ripped_disc(backfill_db):
    """A COMPLETE job with a disc_label gets a ripped_discs row after backfill."""
    engine, factory = backfill_db
    job = await _seed_job(factory, disc_label="THE_MATRIX", disc_uuid="uuid-001")

    await _run_init_db(engine, factory)

    disc = await _get_ripped_disc(factory, "THE_MATRIX")
    assert disc is not None
    assert disc.disc_label == "THE_MATRIX"
    assert disc.disc_uuid == "uuid-001"
    assert disc.job_id == job.id
    # SQLite strips tzinfo on round-trip; compare naive datetimes.
    assert disc.ripped_at.replace(tzinfo=None) == _UPDATED_AT.replace(tzinfo=None)


async def test_complete_job_with_null_disc_label_is_skipped(backfill_db):
    """A COMPLETE job with disc_label=None must not produce a ripped_discs row."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label=None, disc_uuid="uuid-no-label")

    await _run_init_db(engine, factory)

    assert await _count_ripped_discs(factory) == 0


async def test_failed_job_with_label_is_skipped(backfill_db):
    """A FAILED job must not produce a ripped_discs row even if disc_label is set."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label="BAD_DISC", status=JobStatus.FAILED)

    await _run_init_db(engine, factory)

    assert await _count_ripped_discs(factory) == 0


async def test_awaiting_selection_job_with_label_is_skipped(backfill_db):
    """An AWAITING_SELECTION job must not produce a ripped_discs row."""
    engine, factory = backfill_db
    await _seed_job(
        factory, disc_label="PENDING_DISC", status=JobStatus.AWAITING_SELECTION
    )

    await _run_init_db(engine, factory)

    assert await _count_ripped_discs(factory) == 0


async def test_existing_ripped_disc_row_is_not_duplicated(backfill_db):
    """If a ripped_discs row already exists for a disc_label, backfill skips it."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label="ALIEN")

    # Insert the ripped_discs row as if a previous backfill (or normal pipeline) ran.
    async with factory() as session:
        session.add(RippedDisc(disc_label="ALIEN"))
        await session.commit()

    await _run_init_db(engine, factory)

    # Still exactly one row.
    assert await _count_ripped_discs(factory) == 1


async def test_multiple_complete_jobs_same_label_inserts_only_one_row(backfill_db):
    """When multiple COMPLETE jobs share the same disc_label, backfill inserts exactly
    one ripped_discs row (the first one encountered; subsequent ones are skipped)."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label="DUPE_DISC")
    await _seed_job(factory, disc_label="DUPE_DISC")
    await _seed_job(factory, disc_label="DUPE_DISC")

    await _run_init_db(engine, factory)

    assert await _count_ripped_discs(factory) == 1


async def test_backfill_is_idempotent_across_multiple_init_db_calls(backfill_db):
    """Calling init_db() multiple times (simulating repeated restarts) must not
    accumulate duplicate ripped_discs rows."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label="GROUNDHOG")

    await _run_init_db(engine, factory)
    await _run_init_db(engine, factory)
    await _run_init_db(engine, factory)

    assert await _count_ripped_discs(factory) == 1


async def test_only_complete_jobs_contribute_rows(backfill_db):
    """Mix of statuses: only the COMPLETE job with a label should produce a row."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label="COMPLETE_DISC", status=JobStatus.COMPLETE)
    await _seed_job(factory, disc_label="FAILED_DISC", status=JobStatus.FAILED)
    await _seed_job(
        factory, disc_label="WAITING_DISC", status=JobStatus.AWAITING_SELECTION
    )
    await _seed_job(factory, disc_label=None, status=JobStatus.COMPLETE)

    await _run_init_db(engine, factory)

    assert await _count_ripped_discs(factory) == 1
    disc = await _get_ripped_disc(factory, "COMPLETE_DISC")
    assert disc is not None


async def test_disc_uuid_may_be_none_on_backfilled_row(backfill_db):
    """A COMPLETE job with disc_uuid=None yields a RippedDisc with disc_uuid=None."""
    engine, factory = backfill_db
    await _seed_job(factory, disc_label="NO_UUID_DISC", disc_uuid=None)

    await _run_init_db(engine, factory)

    disc = await _get_ripped_disc(factory, "NO_UUID_DISC")
    assert disc is not None
    assert disc.disc_uuid is None
