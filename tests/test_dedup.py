"""Tests for duplicate-disc detection in _process_jobs().

Each test drives _process_jobs() by placing one item on a queue, then
cancels the loop once queue.join() returns (i.e., the item is processed).
_run_pipeline is patched with an AsyncMock so tests never spin up a real
ripping pipeline, and call-count confirms whether the pipeline was skipped.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from jacques.daemon import _process_jobs
from jacques.models.job import Job, JobStatus
from jacques.models.ripped_disc import RippedDisc


# ── helper ────────────────────────────────────────────────────────────────────


async def _run_one(
    db_factory,
    drive_path: str,
    disc_label: str | None,
    disc_uuid: str | None,
    mock_pipeline: AsyncMock,
):
    """Put one disc event on the queue, run _process_jobs until it is processed,
    then cancel the loop.  Returns the Job row created for that event.

    _run_pipeline is already patched by the caller; this helper adds the
    AsyncSessionLocal patch and drives the queue loop."""
    queue: asyncio.Queue[tuple[str, str | None, str | None]] = asyncio.Queue()
    await queue.put((drive_path, disc_label, disc_uuid))

    with patch("jacques.daemon.AsyncSessionLocal", db_factory):
        task = asyncio.create_task(_process_jobs(queue))
        await asyncio.wait_for(queue.join(), timeout=10.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Return the most-recently created Job row.
    async with db_factory() as db:
        result = await db.execute(
            select(Job).order_by(Job.id.desc()).limit(1)
        )
        return result.scalar_one()


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_uuid_match_sets_duplicate_status(db_factory):
    """Disc whose disc_uuid matches an existing RippedDisc → DUPLICATE_DETECTED,
    no pipeline launched."""
    async with db_factory() as db:
        db.add(RippedDisc(disc_label="ORIGINAL", disc_uuid="uuid-001", job_id=None))
        await db.commit()

    mock_pipeline = AsyncMock()
    with patch("jacques.daemon._run_pipeline", mock_pipeline):
        job = await _run_one(db_factory, "/dev/sr0", "ORIGINAL", "uuid-001", mock_pipeline)

    assert job.status == JobStatus.DUPLICATE_DETECTED
    mock_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_dedup_label_match_no_uuid_sets_duplicate_status(db_factory):
    """Disc with no UUID but a matching disc_label → DUPLICATE_DETECTED,
    no pipeline launched."""
    async with db_factory() as db:
        db.add(RippedDisc(disc_label="SOME_MOVIE", disc_uuid=None, job_id=None))
        await db.commit()

    mock_pipeline = AsyncMock()
    with patch("jacques.daemon._run_pipeline", mock_pipeline):
        job = await _run_one(db_factory, "/dev/sr0", "SOME_MOVIE", None, mock_pipeline)

    assert job.status == JobStatus.DUPLICATE_DETECTED
    mock_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_dedup_uuid_match_overrides_label_mismatch(db_factory):
    """UUID match is primary: even when the label differs, a UUID hit still
    yields DUPLICATE_DETECTED."""
    async with db_factory() as db:
        db.add(RippedDisc(disc_label="OLD_LABEL", disc_uuid="uuid-999", job_id=None))
        await db.commit()

    mock_pipeline = AsyncMock()
    with patch("jacques.daemon._run_pipeline", mock_pipeline):
        # Same UUID, completely different label.
        job = await _run_one(db_factory, "/dev/sr0", "DIFFERENT_LABEL", "uuid-999", mock_pipeline)

    assert job.status == JobStatus.DUPLICATE_DETECTED
    mock_pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_dedup_no_match_launches_pipeline(db_factory):
    """Disc with no entry in ripped_discs → pipeline is launched (job stays DETECTED
    because the mock pipeline does not advance the status)."""
    mock_pipeline = AsyncMock()
    with patch("jacques.daemon._run_pipeline", mock_pipeline):
        job = await _run_one(db_factory, "/dev/sr0", "NEW_DISC", "uuid-new", mock_pipeline)

    assert job.status == JobStatus.DETECTED, (
        f"expected DETECTED (pipeline handed off asynchronously), got {job.status}"
    )
    mock_pipeline.assert_called_once()
    call_kwargs = mock_pipeline.call_args
    assert call_kwargs.args[1] == "/dev/sr0"   # drive_path
    assert call_kwargs.args[2] == "NEW_DISC"   # disc_label


@pytest.mark.asyncio
async def test_dedup_both_identifiers_none_launches_pipeline(db_factory):
    """Disc with disc_uuid=None and disc_label=None skips dedup checks entirely
    and proceeds to launch the pipeline."""
    mock_pipeline = AsyncMock()
    with patch("jacques.daemon._run_pipeline", mock_pipeline):
        job = await _run_one(db_factory, "/dev/sr0", None, None, mock_pipeline)

    assert job.status == JobStatus.DETECTED
    mock_pipeline.assert_called_once()
