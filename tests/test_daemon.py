"""Tests for daemon-level helpers in `jacques.daemon` that aren't already
covered indirectly by `tests/test_pipeline.py`'s end-to-end pipeline runs.

Specifically: `_update_job`'s broadcaster publish path. Every existing
pipeline/daemon test calls `_update_job` without ever setting
`app.state.job_events`, so `getattr(app.state, "job_events", None)` is always
`None` and the publish branch has never actually run against a real
`Broadcaster` + `Job.to_response_dict()`. These tests exercise that path
directly.
"""
from unittest.mock import patch

import pytest
import pytest_asyncio

from jacques.api.app import app
from jacques.daemon import _update_job
from jacques.models.job import Job, JobStatus
from jacques.services.broadcaster import Broadcaster


# ── helpers ───────────────────────────────────────────────────────────────────


async def _create_job(db_factory, *, status: JobStatus = JobStatus.DETECTED) -> int:
    async with db_factory() as db:
        job = Job(drive_path="/dev/sr0", disc_label="TEST_DISC", status=status)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def _get_job(db_factory, job_id: int) -> Job:
    async with db_factory() as db:
        return await db.get(Job, job_id)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def broadcaster():
    """A real Broadcaster we wire into app.state.job_events."""
    return Broadcaster()


@pytest_asyncio.fixture
async def job_events_queue(broadcaster):
    """A queue subscribed to `broadcaster`, so tests can inspect published events."""
    return broadcaster.subscribe()


@pytest_asyncio.fixture(autouse=True)
async def _no_job_events_leak():
    """Guarantees app.state.job_events is absent before and after every test in
    this file, matching test_jobs_api.py's teardown convention — prevents a
    broadcaster set by one test from leaking into others that don't expect it
    (e.g. test_pipeline.py's tests, which rely on it being unset)."""
    if hasattr(app.state, "job_events"):
        del app.state.job_events
    yield
    if hasattr(app.state, "job_events"):
        del app.state.job_events


# ── _update_job publish path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_job_publishes_job_upserted_event(db_factory, broadcaster, job_events_queue):
    """A real _update_job call, with a real Broadcaster wired into
    app.state.job_events, reaches the subscriber queue with the shape produced
    by Job.to_response_dict()."""
    app.state.job_events = broadcaster
    job_id = await _create_job(db_factory)

    with patch("jacques.daemon.AsyncSessionLocal", db_factory):
        await _update_job(job_id, status=JobStatus.RIPPING, progress=42)

    assert job_events_queue.qsize() == 1
    event = job_events_queue.get_nowait()
    assert event["type"] == "job_upserted"

    job = event["job"]
    assert job["id"] == job_id
    assert job["status"] == "ripping"
    assert job["progress"] == 42
    assert isinstance(job["display_name"], str) and job["display_name"]


@pytest.mark.asyncio
async def test_update_job_skips_publish_when_job_events_unset(db_factory):
    """The defensive getattr(..., None) means _update_job neither raises nor
    publishes anything when app.state.job_events hasn't been configured —
    the DB write must still succeed."""
    assert not hasattr(app.state, "job_events")
    job_id = await _create_job(db_factory)

    with patch("jacques.daemon.AsyncSessionLocal", db_factory):
        await _update_job(job_id, status=JobStatus.RIPPING, progress=10)

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.RIPPING
    assert job.progress == 10
