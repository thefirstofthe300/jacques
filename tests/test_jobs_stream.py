"""Tests for GET /api/jobs/stream (the SSE endpoint replacing HTMX polling).

Note on testing approach: httpx's ASGITransport (0.28.x) awaits the whole ASGI
app coroutine to completion — collecting the entire response body — before it
ever hands a `Response` back to the client. That works fine for ordinary
request/response endpoints, but it means a real `httpx.AsyncClient` can't
observe an infinite `StreamingResponse` mid-stream: `client.stream(...)`
would simply hang until the generator exits, which for this endpoint only
happens on client disconnect.

So this file uses two complementary strategies:
- For exact SSE frame formatting, call `stream_jobs` directly with a minimal
  fake `Request` stub and drive its `StreamingResponse.body_iterator`
  directly — deterministic, no transport involved.
- For the leak-prevention property (unsubscribe on disconnect), drive the
  real FastAPI app through `httpx.AsyncClient`/`ASGITransport`, run the
  request in a background task, and cancel that task to simulate a client
  going away — this exercises the real ASGI call stack end to end and
  confirms `broadcaster.unsubscribe` actually runs.
- The 503 (no broadcaster configured) case doesn't stream at all, so a
  plain request through `api_client` is sufficient.
"""
import asyncio
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import jacques.api.routes.jobs as jobs_module
from jacques.api.app import app
from jacques.services.broadcaster import Broadcaster


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def broadcaster():
    """A real Broadcaster we wire into app.state.job_events."""
    return Broadcaster()


@pytest_asyncio.fixture
async def api_client(broadcaster):
    """AsyncClient wired to the FastAPI app with app.state.job_events set.

    The stream endpoint doesn't touch the database, so unlike test_jobs_api's
    api_client fixture, this one doesn't override get_db or set rerun_queue.
    """
    app.state.job_events = broadcaster

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    if hasattr(app.state, "job_events"):
        del app.state.job_events


class _FakeAppState:
    def __init__(self, job_events):
        self.job_events = job_events


class _FakeApp:
    def __init__(self, job_events):
        self.state = _FakeAppState(job_events)


class _FakeRequest:
    """Minimal stand-in for fastapi.Request, just enough for `stream_jobs`:
    `request.app.state.job_events` and an awaitable, externally-toggleable
    `is_disconnected()`.
    """

    def __init__(self, job_events):
        self.app = _FakeApp(job_events)
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


# ── SSE frame formatting ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_yields_job_update_frame_on_publish(broadcaster):
    """A published event must come out as a single `event: job-update`
    SSE frame carrying the JSON-encoded event dict verbatim."""
    fake_request = _FakeRequest(broadcaster)
    response = await jobs_module.stream_jobs(fake_request)

    assert response.media_type == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"

    event = {"type": "job_upserted", "job": {"id": 42, "status": "ripping"}}
    broadcaster.publish(event)

    frame = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=1)

    assert frame == f"event: job-update\ndata: {json.dumps(event)}\n\n"


@pytest.mark.asyncio
async def test_stream_yields_job_deleted_frame(broadcaster):
    fake_request = _FakeRequest(broadcaster)
    response = await jobs_module.stream_jobs(fake_request)

    event = {"type": "job_deleted", "job_id": 7}
    broadcaster.publish(event)

    frame = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=1)

    assert frame == f"event: job-update\ndata: {json.dumps(event)}\n\n"


@pytest.mark.asyncio
async def test_stream_unsubscribes_on_disconnect(broadcaster):
    """Once the client is detected as disconnected, the generator must stop
    AND unsubscribe its queue from the broadcaster — the core leak-prevention
    property of this endpoint."""
    fake_request = _FakeRequest(broadcaster)
    response = await jobs_module.stream_jobs(fake_request)

    assert len(broadcaster._subscribers) == 1

    fake_request.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(response.body_iterator.__anext__(), timeout=1)

    assert len(broadcaster._subscribers) == 0


@pytest.mark.asyncio
async def test_stream_unsubscribes_even_if_generator_is_cancelled(broadcaster):
    """Cancellation mid-wait (e.g. the ASGI server tearing down the response
    task on a dropped connection) must still hit the `finally` and unsubscribe,
    not just the graceful is_disconnected()-detected path."""
    fake_request = _FakeRequest(broadcaster)
    response = await jobs_module.stream_jobs(fake_request)

    assert len(broadcaster._subscribers) == 1

    task = asyncio.create_task(response.body_iterator.__anext__())
    await asyncio.sleep(0)  # let it reach the queue.get() wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(broadcaster._subscribers) == 0


# ── end-to-end disconnect through the real ASGI stack ────────────────────────


@pytest.mark.asyncio
async def test_stream_unsubscribes_when_real_client_disconnects(api_client, broadcaster):
    """Drive the endpoint through the actual FastAPI app/ASGI stack (not the
    fake Request stub above) and simulate a client going away by cancelling
    the in-flight request task. The subscriber queue must be removed from the
    broadcaster once the ASGI call stack unwinds."""
    task = asyncio.create_task(api_client.get("/api/jobs/stream"))

    for _ in range(200):
        if broadcaster._subscribers:
            break
        await asyncio.sleep(0.01)
    assert len(broadcaster._subscribers) == 1, "endpoint never subscribed"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(broadcaster._subscribers) == 0


# ── 503 — no broadcaster wired up ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_503_when_job_events_not_set():
    """If app.state.job_events isn't set (daemon hasn't wired up the
    broadcaster yet), the endpoint returns 503 rather than raising."""
    if hasattr(app.state, "job_events"):
        del app.state.job_events

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/jobs/stream")

    assert response.status_code == 503
