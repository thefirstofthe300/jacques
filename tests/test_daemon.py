"""Tests for daemon-level helpers in `jacques.daemon` that aren't already
covered indirectly by `tests/test_pipeline.py`'s end-to-end pipeline runs.

Specifically: `_update_job`'s broadcaster publish path. Every existing
pipeline/daemon test calls `_update_job` without ever setting
`app.state.job_events`, so `getattr(app.state, "job_events", None)` is always
`None` and the publish branch has never actually run against a real
`Broadcaster` + `Job.to_response_dict()`. These tests exercise that path
directly.
"""
import asyncio
import logging
from unittest.mock import patch

import pytest
import pytest_asyncio

from jacques.api.app import app
from jacques.daemon import _redact_secrets_from_log_record, _shutdown_watcher, _update_job
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


# ── Secret redaction in httpx request logs ────────────────────────────────────


def _log_record(msg, args=()):
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, None)


def test_redact_secrets_masks_api_key_in_rendered_message():
    record = _log_record(
        'HTTP Request: GET https://api.themoviedb.org/3/search/movie?api_key=b34eb5386c2b1b8a&query=Edge "HTTP/1.1 200 OK"'
    )

    _redact_secrets_from_log_record(record)

    message = record.getMessage()
    assert "b34eb5386c2b1b8a" not in message
    assert "api_key=<redacted>" in message
    assert "query=Edge" in message


def test_redact_secrets_masks_api_key_passed_via_percent_args():
    record = _log_record(
        'HTTP Request: %s %s "%s %d %s"',
        ("GET", "https://api.themoviedb.org/3/search/movie?api_key=b34eb5386c2b1b8a", "HTTP/1.1", 200, "OK"),
    )

    _redact_secrets_from_log_record(record)

    message = record.getMessage()
    assert "b34eb5386c2b1b8a" not in message
    assert "api_key=<redacted>" in message


def test_redact_secrets_leaves_non_secret_urls_unchanged():
    record = _log_record('HTTP Request: GET https://api.thediscdb.com/graphql "HTTP/1.1 200 OK"')
    original = record.getMessage()

    _redact_secrets_from_log_record(record)

    assert record.getMessage() == original


# ── Graceful shutdown ──────────────────────────────────────────────────────────


class _FakeServer:
    should_exit = False


@pytest.mark.asyncio
async def test_shutdown_watcher_cancels_tasks_once_should_exit():
    """Uvicorn only manages its own listener on a signal; `_shutdown_watcher`
    is what makes the disc detector, job/rerun consumers, and any in-flight
    disc pipeline actually stop once `server.should_exit` flips."""
    server = _FakeServer()

    async def _never_ending():
        await asyncio.sleep(3600)

    task_a = asyncio.create_task(_never_ending(), name="a")
    task_b = asyncio.create_task(_never_ending(), name="b")
    pipeline_task = asyncio.create_task(_never_ending(), name="pipeline")
    pipeline_tasks = {pipeline_task}

    watcher = asyncio.create_task(_shutdown_watcher(server, [task_a, task_b], pipeline_tasks))
    await asyncio.sleep(0)  # let the watcher start polling
    server.should_exit = True

    await asyncio.wait_for(watcher, timeout=2)
    await asyncio.gather(task_a, task_b, pipeline_task, return_exceptions=True)

    assert task_a.cancelled()
    assert task_b.cancelled()
    assert pipeline_task.cancelled()


@pytest.mark.asyncio
async def test_shutdown_watcher_does_nothing_while_running():
    server = _FakeServer()

    async def _never_ending():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_never_ending())
    watcher = asyncio.create_task(_shutdown_watcher(server, [task], set()))

    await asyncio.sleep(0.1)  # a few poll intervals with should_exit still False

    assert not watcher.done()
    assert not task.cancelled()

    watcher.cancel()
    task.cancel()
    await asyncio.gather(watcher, task, return_exceptions=True)
