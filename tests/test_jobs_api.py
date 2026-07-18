"""Tests for POST /api/jobs/{job_id}/rerun/{stage} and POST /api/jobs/{job_id}/select/{tmdb_id}.

Uses an in-memory SQLite DB (db_factory fixture), an httpx AsyncClient backed
by the real FastAPI app, and a mock asyncio.Queue injected into app.state so
we can assert enqueue behaviour without running the daemon.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from jacques.api.app import app
from jacques.database import get_db
from jacques.models.job import DiscType, Job, JobStatus
from jacques.services.metadata import MediaInfo


# ── helpers ───────────────────────────────────────────────────────────────────


async def _create_job(
    db_factory,
    *,
    status: JobStatus = JobStatus.FAILED,
    drive_path: str = "/dev/sr0",
    disc_label: str | None = "TEST_DISC",
    disc_type: DiscType = DiscType.UNKNOWN,
    progress: int = 50,
    error_message: str | None = "something went wrong",
    candidates: str | None = None,
    titles_json: str | None = None,
    episode_assignments: str | None = None,
    selected_title_id: int | None = None,
) -> int:
    async with db_factory() as db:
        job = Job(
            drive_path=drive_path,
            disc_label=disc_label,
            disc_type=disc_type,
            status=status,
            progress=progress,
            error_message=error_message,
            candidates=candidates,
            titles_json=titles_json,
            episode_assignments=episode_assignments,
            selected_title_id=selected_title_id,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def _get_job(db_factory, job_id: int) -> Job:
    async with db_factory() as db:
        return await db.get(Job, job_id)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def mock_queue():
    """A real asyncio.Queue we can inspect after the request."""
    return asyncio.Queue()


@pytest_asyncio.fixture
async def api_client(db_factory, mock_queue):
    """AsyncClient wired to the FastAPI app with:
    - get_db overridden to use the in-memory db_factory
    - app.state.rerun_queue set to mock_queue
    """
    async def _override_get_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.state.rerun_queue = mock_queue

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    if hasattr(app.state, "rerun_queue"):
        del app.state.rerun_queue


# ── 404 — job not found ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_404_job_not_found(api_client):
    response = await api_client.post("/api/jobs/99999/rerun/fetching_metadata")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ── 400 — invalid stage ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_400_invalid_stage(api_client, db_factory):
    job_id = await _create_job(db_factory)
    response = await api_client.post(f"/api/jobs/{job_id}/rerun/bad_stage")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "bad_stage" in detail
    assert "Valid stages" in detail


# ── 409 — active job ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_409_active_job_ripping(api_client, db_factory):
    """A job in RIPPING status is active; rerun must be rejected."""
    job_id = await _create_job(db_factory, status=JobStatus.RIPPING, error_message=None)
    response = await api_client.post(f"/api/jobs/{job_id}/rerun/fetching_metadata")
    assert response.status_code == 409
    assert "active" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rerun_409_active_job_transcoding_status(api_client, db_factory):
    """A job in TRANSCODING status is active even when stage=transcoding is requested."""
    job_id = await _create_job(db_factory, status=JobStatus.TRANSCODING, error_message=None)
    response = await api_client.post(f"/api/jobs/{job_id}/rerun/transcoding")
    assert response.status_code == 409
    assert "active" in response.json()["detail"].lower()


# ── 409 — temp file prerequisites missing ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_409_transcoding_no_raw_done(api_client, db_factory, tmp_path, monkeypatch):
    """stage=transcoding without raw/.done marker → 409."""
    import jacques.api.routes.jobs as jobs_module

    monkeypatch.setattr(jobs_module.settings, "temp_path", tmp_path)

    job_id = await _create_job(db_factory)
    # raw dir exists but no .done marker
    raw_dir = tmp_path / str(job_id) / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "title_t00.mkv").write_bytes(b"partial rip")

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/transcoding")
    assert response.status_code == 409
    assert "raw" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rerun_409_organizing_no_transcoded_done(api_client, db_factory, tmp_path, monkeypatch):
    """stage=organizing without transcoded/.done marker → 409."""
    import jacques.api.routes.jobs as jobs_module

    monkeypatch.setattr(jobs_module.settings, "temp_path", tmp_path)

    job_id = await _create_job(db_factory)
    # transcoded dir exists but no .done marker
    transcoded_dir = tmp_path / str(job_id) / "transcoded"
    transcoded_dir.mkdir(parents=True)
    (transcoded_dir / "film.mkv").write_bytes(b"incomplete")

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/organizing")
    assert response.status_code == 409
    assert "transcoded" in response.json()["detail"].lower()


# ── 202 happy path — generic stage (fetching_metadata) ───────────────────────


@pytest.mark.asyncio
async def test_rerun_202_fetching_metadata(api_client, db_factory, mock_queue):
    """FAILED job reruns from fetching_metadata:
    - 202 response with job_id and stage
    - DB: status=FETCHING_METADATA, error_message=None, progress=0
    - Queue receives (job_id, JobStatus.FETCHING_METADATA)
    """
    job_id = await _create_job(db_factory, status=JobStatus.FAILED, progress=42)

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/fetching_metadata")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["stage"] == "fetching_metadata"

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.FETCHING_METADATA
    assert job.error_message is None
    assert job.progress == 0

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.FETCHING_METADATA)


@pytest.mark.asyncio
async def test_rerun_202_identifying(api_client, db_factory, mock_queue):
    """COMPLETE job reruns from identifying (no temp file check needed).
    - 202 response
    - DB: status=IDENTIFYING, error_message=None, progress=0
    - Queue receives (job_id, JobStatus.IDENTIFYING)
    """
    job_id = await _create_job(
        db_factory, status=JobStatus.COMPLETE, progress=100, error_message=None
    )

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/identifying")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["stage"] == "identifying"

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.IDENTIFYING
    assert job.error_message is None
    assert job.progress == 0

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.IDENTIFYING)


# ── 202 — transcoding with raw/.done present ─────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_202_transcoding_with_raw_done(api_client, db_factory, mock_queue, tmp_path, monkeypatch):
    """FAILED job, stage=transcoding, raw/.done exists:
    - 202 response
    - DB: status=TRANSCODING, error_message=None, progress=0
    - Queue receives (job_id, JobStatus.TRANSCODING)
    """
    import jacques.api.routes.jobs as jobs_module

    monkeypatch.setattr(jobs_module.settings, "temp_path", tmp_path)

    job_id = await _create_job(db_factory, status=JobStatus.FAILED, progress=30)

    # Create the raw/.done marker that the endpoint requires
    raw_dir = tmp_path / str(job_id) / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / ".done").touch()
    (raw_dir / "title_t00.mkv").write_bytes(b"raw ripped content")

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/transcoding")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["stage"] == "transcoding"

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.TRANSCODING
    assert job.error_message is None
    assert job.progress == 0

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.TRANSCODING)


# ── 202 — organizing with transcoded/.done present ───────────────────────────


@pytest.mark.asyncio
async def test_rerun_202_organizing_with_transcoded_done(api_client, db_factory, mock_queue, tmp_path, monkeypatch):
    """FAILED job, stage=organizing, transcoded/.done exists:
    - 202 response
    - DB: status=ORGANIZING, error_message=None, progress=0
    - Queue receives (job_id, JobStatus.ORGANIZING)
    """
    import jacques.api.routes.jobs as jobs_module

    monkeypatch.setattr(jobs_module.settings, "temp_path", tmp_path)

    job_id = await _create_job(db_factory, status=JobStatus.FAILED, progress=75)

    # Create the transcoded/.done marker
    transcoded_dir = tmp_path / str(job_id) / "transcoded"
    transcoded_dir.mkdir(parents=True)
    (transcoded_dir / ".done").touch()
    (transcoded_dir / "film.mkv").write_bytes(b"h265 content")

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/organizing")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["stage"] == "organizing"

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.ORGANIZING
    assert job.error_message is None
    assert job.progress == 0

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.ORGANIZING)


# ── 202 — ripping with titles_json present ───────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_202_ripping(api_client, db_factory, mock_queue):
    """FAILED job, stage=ripping, titles_json populated (already resolved during
    a prior IDENTIFYING pass):
    - 202 response with job_id and stage
    - DB: status=RIPPING, error_message=None, progress=0
    - Queue receives (job_id, JobStatus.RIPPING)
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.FAILED,
        titles_json=_TWO_TITLES,
        progress=65,
    )

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/ripping")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["stage"] == "ripping"

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.RIPPING
    assert job.error_message is None
    assert job.progress == 0

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.RIPPING)


# ── 409 — ripping without titles_json ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerun_409_ripping_no_titles_json(api_client, db_factory, mock_queue):
    """stage=ripping without a titles_json (no disc titles were ever recorded,
    e.g. the job crashed before finishing IDENTIFYING) → 409, and the rerun_queue
    must not be touched."""
    job_id = await _create_job(db_factory, status=JobStatus.FAILED, titles_json=None)

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/ripping")

    assert response.status_code == 409
    detail = response.json()["detail"].lower()
    assert "identifying" in detail or "titles" in detail

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.FAILED

    assert mock_queue.empty()


@pytest.mark.asyncio
async def test_rerun_409_active_job_takes_priority_over_ripping_titles_check(
    api_client, db_factory, mock_queue
):
    """When a job is active (e.g. TRANSCODING) AND has no titles_json, stage=ripping
    must still fail on the 'currently active' guard rather than the titles_json
    precondition — the active-job check runs first."""
    job_id = await _create_job(
        db_factory, status=JobStatus.TRANSCODING, titles_json=None, error_message=None
    )

    response = await api_client.post(f"/api/jobs/{job_id}/rerun/ripping")

    assert response.status_code == 409
    assert "active" in response.json()["detail"].lower()

    assert mock_queue.empty()


# ── 503 — service not ready (no rerun_queue in app.state) ────────────────────


@pytest.mark.asyncio
async def test_rerun_503_service_not_ready(db_factory):
    """If rerun_queue is absent from app.state, the endpoint returns 503 rather
    than raising an AttributeError."""
    async def _override_get_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    if hasattr(app.state, "rerun_queue"):
        del app.state.rerun_queue

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            job_id = await _create_job(db_factory, status=JobStatus.FAILED)
            response = await client.post(f"/api/jobs/{job_id}/rerun/identifying")
        assert response.status_code == 503
        assert "not ready" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "rerun_queue"):
            del app.state.rerun_queue


# ── queue isolation — only one item enqueued per request ─────────────────────


@pytest.mark.asyncio
async def test_rerun_enqueues_exactly_one_item(api_client, db_factory, mock_queue):
    """Confirm exactly one tuple is put on the queue per successful rerun."""
    job_id = await _create_job(db_factory, status=JobStatus.FAILED)

    await api_client.post(f"/api/jobs/{job_id}/rerun/fetching_metadata")

    assert mock_queue.qsize() == 1


# ── select_match — direct TMDB ID lookup (no candidates) ─────────────────────


@pytest.mark.asyncio
async def test_select_match_direct_tmdb_id(api_client, db_factory, mock_queue):
    """AWAITING_SELECTION job with candidates=None:
    - MetadataService.lookup_by_id is called with the given tmdb_id and disc_type
    - 202 response with job_id and tmdb_id
    - DB: title/year/disc_type updated, candidates=None, status=TRANSCODING, progress=0
    - Queue receives (job_id, JobStatus.TRANSCODING)
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_SELECTION,
        disc_type=DiscType.MOVIE,
        candidates=None,
        error_message=None,
        progress=25,
    )

    fake_media = MediaInfo(
        title="Inception",
        year=2010,
        disc_type=DiscType.MOVIE,
        tmdb_id=27205,
    )

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        mock_cls.return_value.lookup_by_id = AsyncMock(return_value=fake_media)

        response = await api_client.post(f"/api/jobs/{job_id}/select/27205")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["tmdb_id"] == 27205

    # Verify MetadataService was constructed and called correctly
    mock_cls.assert_called_once()
    mock_cls.return_value.lookup_by_id.assert_awaited_once_with(27205, DiscType.MOVIE)

    job = await _get_job(db_factory, job_id)
    assert job.title == "Inception"
    assert job.year == 2010
    assert job.disc_type == DiscType.MOVIE
    assert job.tmdb_id == 27205
    assert job.candidates is None
    assert job.status == JobStatus.TRANSCODING
    assert job.progress == 0

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.TRANSCODING)


@pytest.mark.asyncio
async def test_select_match_direct_tmdb_id_not_found(api_client, db_factory):
    """AWAITING_SELECTION job with candidates=None, but lookup_by_id raises ValueError:
    - 404 response
    - DB unchanged
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_SELECTION,
        disc_type=DiscType.MOVIE,
        candidates=None,
        error_message=None,
        progress=0,
    )

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        mock_cls.return_value.lookup_by_id = AsyncMock(
            side_effect=ValueError("TMDB ID 99999 not found")
        )

        response = await api_client.post(f"/api/jobs/{job_id}/select/99999")

    assert response.status_code == 404
    assert response.json()["detail"] == "TMDB ID not found"

    # DB must be unchanged
    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_SELECTION
    assert job.title is None


@pytest.mark.asyncio
async def test_select_match_wrong_status_no_candidates(api_client, db_factory):
    """Job with status=FAILED and candidates=None:
    - Status guard fires before any MetadataService call
    - 409 response
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.FAILED,
        disc_type=DiscType.MOVIE,
        candidates=None,
    )

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        response = await api_client.post(f"/api/jobs/{job_id}/select/27205")

        # MetadataService must never be touched
        mock_cls.assert_not_called()

    assert response.status_code == 409
    assert "awaiting selection" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_select_match_409_ripping_status(api_client, db_factory):
    """Job with status=RIPPING: select_match only accepts AWAITING_SELECTION, so
    a RIPPING job must be rejected with 409."""
    job_id = await _create_job(
        db_factory,
        status=JobStatus.RIPPING,
        disc_type=DiscType.MOVIE,
        candidates='[{"tmdb_id": 550, "title": "Fight Club", "year": 1999, "disc_type": "movie", "overview": ""}]',
        error_message=None,
    )

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        response = await api_client.post(f"/api/jobs/{job_id}/select/550")

        # MetadataService must never be touched
        mock_cls.assert_not_called()

    assert response.status_code == 409
    assert "awaiting selection" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_select_match_503_no_rerun_queue(db_factory):
    """If rerun_queue is absent from app.state, the select endpoint returns 503
    rather than raising an AttributeError."""
    async def _override_get_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    if hasattr(app.state, "rerun_queue"):
        del app.state.rerun_queue

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            job_id = await _create_job(
                db_factory,
                status=JobStatus.AWAITING_SELECTION,
                disc_type=DiscType.MOVIE,
                candidates='[{"tmdb_id": 550, "title": "Fight Club", "year": 1999, "disc_type": "movie", "overview": ""}]',
                error_message=None,
            )

            fake_media = MediaInfo(
                title="Fight Club",
                year=1999,
                disc_type=DiscType.MOVIE,
                tmdb_id=550,
            )

            with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
                mock_cls.return_value.lookup_by_id = AsyncMock(return_value=fake_media)
                response = await client.post(f"/api/jobs/{job_id}/select/550")

        assert response.status_code == 503
        assert "not ready" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "rerun_queue"):
            del app.state.rerun_queue


@pytest.mark.asyncio
async def test_select_match_tmdb_id_not_in_candidates_falls_through(api_client, db_factory, mock_queue):
    """When a job has stored candidates but the given TMDB ID is not among them,
    the endpoint falls through to a direct TMDb lookup instead of returning 404."""
    stored = '[{"tmdb_id": 550, "title": "Fight Club", "year": 1999, "disc_type": "movie", "overview": ""}]'
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_SELECTION,
        disc_type=DiscType.MOVIE,
        candidates=stored,
        error_message=None,
        progress=0,
    )

    fake_media = MediaInfo(title="The Patriot", year=2000, disc_type=DiscType.MOVIE, tmdb_id=9659)

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        mock_cls.return_value.lookup_by_id = AsyncMock(return_value=fake_media)
        response = await api_client.post(f"/api/jobs/{job_id}/select/9659")

    assert response.status_code == 202
    mock_cls.return_value.lookup_by_id.assert_awaited_once_with(9659, DiscType.MOVIE)

    job = await _get_job(db_factory, job_id)
    assert job.title == "The Patriot"
    assert job.year == 2000
    assert job.tmdb_id == 9659
    assert job.candidates is None
    assert job.status == JobStatus.TRANSCODING


# ── select_match — RIPPING_AWAITING_SELECTION status ─────────────────────────


@pytest.mark.asyncio
async def test_select_match_ripping_awaiting_selection_sets_ripping(
    api_client, db_factory, mock_queue
):
    """RIPPING_AWAITING_SELECTION job with a matching stored candidate:
    - 202 response with job_id and tmdb_id
    - DB: title/year/disc_type/tmdb_id set from the candidate, candidates=None
    - DB: status becomes RIPPING (not TRANSCODING) and progress is left untouched
    - rerun_queue is NOT touched (this path resumes ripping, not transcoding)
    """
    stored = '[{"tmdb_id": 550, "title": "Fight Club", "year": 1999, "disc_type": "movie", "overview": ""}]'
    job_id = await _create_job(
        db_factory,
        status=JobStatus.RIPPING_AWAITING_SELECTION,
        disc_type=DiscType.MOVIE,
        candidates=stored,
        error_message="paused mid-rip",
        progress=40,
    )

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        response = await api_client.post(f"/api/jobs/{job_id}/select/550")

        # Matching candidate found in stored JSON; no TMDb lookup needed
        mock_cls.assert_not_called()

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["tmdb_id"] == 550

    job = await _get_job(db_factory, job_id)
    assert job.title == "Fight Club"
    assert job.year == 1999
    assert job.tmdb_id == 550
    assert job.disc_type == DiscType.MOVIE
    assert job.candidates is None
    assert job.error_message is None
    assert job.status == JobStatus.RIPPING
    assert job.progress == 40

    # rerun_queue must not be touched for the still-ripping path
    assert mock_queue.empty()


@pytest.mark.asyncio
async def test_select_match_ripping_awaiting_selection_direct_tmdb_id(
    api_client, db_factory, mock_queue
):
    """RIPPING_AWAITING_SELECTION job where the given tmdb_id is not among the
    stored candidates: falls through to a direct TMDb lookup, still ends in
    RIPPING (not TRANSCODING), and still never touches rerun_queue."""
    stored = '[{"tmdb_id": 550, "title": "Fight Club", "year": 1999, "disc_type": "movie", "overview": ""}]'
    job_id = await _create_job(
        db_factory,
        status=JobStatus.RIPPING_AWAITING_SELECTION,
        disc_type=DiscType.MOVIE,
        candidates=stored,
        error_message=None,
        progress=10,
    )

    fake_media = MediaInfo(title="The Patriot", year=2000, disc_type=DiscType.MOVIE, tmdb_id=9659)

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        mock_cls.return_value.lookup_by_id = AsyncMock(return_value=fake_media)
        response = await api_client.post(f"/api/jobs/{job_id}/select/9659")

    assert response.status_code == 202
    mock_cls.return_value.lookup_by_id.assert_awaited_once_with(9659, DiscType.MOVIE)

    job = await _get_job(db_factory, job_id)
    assert job.title == "The Patriot"
    assert job.year == 2000
    assert job.tmdb_id == 9659
    assert job.candidates is None
    assert job.status == JobStatus.RIPPING

    assert mock_queue.empty()


# ── DELETE /api/jobs/{job_id} ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_204_complete_job(api_client, db_factory):
    """DELETE on a COMPLETE job returns 204 and removes the row from the DB."""
    job_id = await _create_job(db_factory, status=JobStatus.COMPLETE, error_message=None, progress=100)

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 204
    assert await _get_job(db_factory, job_id) is None


@pytest.mark.asyncio
async def test_delete_204_failed_job(api_client, db_factory):
    """DELETE on a FAILED job returns 204 and removes the row from the DB."""
    job_id = await _create_job(db_factory, status=JobStatus.FAILED)

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 204
    assert await _get_job(db_factory, job_id) is None


@pytest.mark.asyncio
async def test_delete_204_duplicate_detected_job(api_client, db_factory):
    """DELETE on a DUPLICATE_DETECTED job returns 204 and removes the row from the DB."""
    job_id = await _create_job(
        db_factory, status=JobStatus.DUPLICATE_DETECTED, error_message=None
    )

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 204
    assert await _get_job(db_factory, job_id) is None


@pytest.mark.asyncio
async def test_delete_204_awaiting_selection_job(api_client, db_factory):
    """DELETE on an AWAITING_SELECTION job returns 204 and removes the row from the DB."""
    job_id = await _create_job(
        db_factory, status=JobStatus.AWAITING_SELECTION, error_message=None
    )

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 204
    assert await _get_job(db_factory, job_id) is None


@pytest.mark.asyncio
async def test_delete_409_active_job(api_client, db_factory):
    """DELETE on a RIPPING (active) job returns 409."""
    job_id = await _create_job(db_factory, status=JobStatus.RIPPING, error_message=None)

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 409
    assert "active" in response.json()["detail"].lower()
    # Job must still exist
    assert await _get_job(db_factory, job_id) is not None


@pytest.mark.asyncio
async def test_delete_404_job_not_found(api_client):
    """DELETE on a non-existent job_id returns 404."""
    response = await api_client.delete("/api/jobs/99999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ── POST /api/jobs/{job_id}/rerip ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerip_202_duplicate_detected(api_client, db_factory, mock_queue):
    """rerip on a DUPLICATE_DETECTED job:
    - 202 response with job_id
    - DB: status=IDENTIFYING, progress=0, error_message=None
    - Queue receives (job_id, JobStatus.IDENTIFYING)
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.DUPLICATE_DETECTED,
        progress=50,
        error_message="duplicate disc",
    )

    response = await api_client.post(f"/api/jobs/{job_id}/rerip")

    assert response.status_code == 202
    assert response.json()["job_id"] == job_id

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.IDENTIFYING
    assert job.progress == 0
    assert job.error_message is None

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.IDENTIFYING)


@pytest.mark.asyncio
async def test_rerip_409_wrong_status(api_client, db_factory):
    """rerip on a job that is not DUPLICATE_DETECTED returns 409."""
    job_id = await _create_job(db_factory, status=JobStatus.FAILED)

    response = await api_client.post(f"/api/jobs/{job_id}/rerip")

    assert response.status_code == 409
    assert "duplicate" in response.json()["detail"].lower()

    # Job status must be unchanged
    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_rerip_404_job_not_found(api_client):
    """rerip on a non-existent job_id returns 404."""
    response = await api_client.post("/api/jobs/99999/rerip")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rerip_503_no_rerun_queue(db_factory):
    """If rerun_queue is absent from app.state, rerip returns 503 instead of
    raising AttributeError."""
    async def _override_get_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    if hasattr(app.state, "rerun_queue"):
        del app.state.rerun_queue

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            job_id = await _create_job(
                db_factory,
                status=JobStatus.DUPLICATE_DETECTED,
                error_message=None,
            )
            response = await client.post(f"/api/jobs/{job_id}/rerip")

        assert response.status_code == 503
        assert "not ready" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "rerun_queue"):
            del app.state.rerun_queue


# ── helpers for parsed_titles fixtures ────────────────────────────────────────


def _title(title_id: int, name: str = "title") -> dict:
    """A minimal TitleInfo-shaped dict, keyed by 'id' as parsed_titles expects."""
    return {
        "id": title_id,
        "name": name,
        "duration_seconds": 3600,
        "filename": f"title_t{title_id:02d}.mkv",
        "chapter_count": 12,
        "expected_bytes": 4_000_000_000,
    }


_TWO_TITLES = json.dumps([_title(0, "Episode A"), _title(1, "Episode B")])


# ── POST /api/jobs/{job_id}/assign-episodes ───────────────────────────────────


@pytest.mark.asyncio
async def test_assign_episodes_404_job_not_found(api_client):
    response = await api_client.post(
        "/api/jobs/99999/assign-episodes",
        json=[{"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"}],
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_assign_episodes_409_wrong_status(api_client, db_factory):
    """Job not in AWAITING_EPISODE_ASSIGNMENT (e.g. still RIPPING) is rejected."""
    job_id = await _create_job(
        db_factory, status=JobStatus.RIPPING, titles_json=_TWO_TITLES, error_message=None
    )

    response = await api_client.post(
        f"/api/jobs/{job_id}/assign-episodes",
        json=[
            {"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"},
            {"title_id": 1, "season": 1, "episode": 2, "name": "Second"},
        ],
    )

    assert response.status_code == 409
    assert "awaiting episode assignment" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.RIPPING
    assert job.episode_assignments is None


@pytest.mark.asyncio
async def test_assign_episodes_409_complete_status(api_client, db_factory):
    """Job already COMPLETE is rejected too."""
    job_id = await _create_job(
        db_factory, status=JobStatus.COMPLETE, titles_json=_TWO_TITLES, error_message=None
    )

    response = await api_client.post(
        f"/api/jobs/{job_id}/assign-episodes",
        json=[
            {"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"},
            {"title_id": 1, "season": 1, "episode": 2, "name": "Second"},
        ],
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_assign_episodes_400_missing_title_id(api_client, db_factory):
    """Submitted title_ids missing one of the parsed_titles ids → 400."""
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_EPISODE_ASSIGNMENT,
        titles_json=_TWO_TITLES,
        error_message=None,
    )

    response = await api_client.post(
        f"/api/jobs/{job_id}/assign-episodes",
        json=[{"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"}],
    )

    assert response.status_code == 400
    assert "missing title_ids" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_EPISODE_ASSIGNMENT
    assert job.episode_assignments is None


@pytest.mark.asyncio
async def test_assign_episodes_400_unknown_title_id(api_client, db_factory):
    """Submitted title_ids include one not present in parsed_titles → 400."""
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_EPISODE_ASSIGNMENT,
        titles_json=_TWO_TITLES,
        error_message=None,
    )

    response = await api_client.post(
        f"/api/jobs/{job_id}/assign-episodes",
        json=[
            {"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"},
            {"title_id": 1, "season": 1, "episode": 2, "name": "Second"},
            {"title_id": 99, "season": 1, "episode": 3, "name": "Extra"},
        ],
    )

    assert response.status_code == 400
    assert "unknown title_ids" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_EPISODE_ASSIGNMENT
    assert job.episode_assignments is None


@pytest.mark.asyncio
async def test_assign_episodes_400_duplicate_title_id(api_client, db_factory):
    """Duplicate title_id in the submitted body (with another title missing) → 400."""
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_EPISODE_ASSIGNMENT,
        titles_json=_TWO_TITLES,
        error_message=None,
    )

    response = await api_client.post(
        f"/api/jobs/{job_id}/assign-episodes",
        json=[
            {"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"},
            {"title_id": 0, "season": 1, "episode": 2, "name": "Duplicate"},
        ],
    )

    assert response.status_code == 400
    assert "duplicate title_ids" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_EPISODE_ASSIGNMENT
    assert job.episode_assignments is None


@pytest.mark.asyncio
async def test_assign_episodes_400_duplicate_title_id_with_matching_set(api_client, db_factory):
    """Duplicate title_id where the deduped set of ids still equals parsed_title_ids
    exactly (0, 0, 1 -> {0, 1}) must still be rejected, not silently accepted with a
    dropped assignment.
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_EPISODE_ASSIGNMENT,
        titles_json=_TWO_TITLES,
        error_message=None,
    )

    response = await api_client.post(
        f"/api/jobs/{job_id}/assign-episodes",
        json=[
            {"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"},
            {"title_id": 0, "season": 1, "episode": 2, "name": "Duplicate"},
            {"title_id": 1, "season": 1, "episode": 3, "name": "Second"},
        ],
    )

    assert response.status_code == 400
    assert "duplicate title_ids" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_EPISODE_ASSIGNMENT
    assert job.episode_assignments is None


@pytest.mark.asyncio
async def test_assign_episodes_202_happy_path(api_client, db_factory, mock_queue):
    """Valid payload covering exactly the parsed_titles ids:
    - 202 response
    - DB: episode_assignments stored as {"<title_id>": {season, episode, name}},
      status=TRANSCODING, progress=0, error_message=None
    - Queue receives (job_id, JobStatus.TRANSCODING)
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_EPISODE_ASSIGNMENT,
        titles_json=_TWO_TITLES,
        error_message="stale error",
        progress=10,
    )

    payload = [
        {"title_id": 0, "season": 1, "episode": 1, "name": "Pilot"},
        {"title_id": 1, "season": 1, "episode": 2, "name": "Second"},
    ]

    response = await api_client.post(f"/api/jobs/{job_id}/assign-episodes", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["assigned"] == 2

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.TRANSCODING
    assert job.progress == 0
    assert job.error_message is None

    stored = json.loads(job.episode_assignments)
    assert stored == {
        "0": {"season": 1, "episode": 1, "name": "Pilot"},
        "1": {"season": 1, "episode": 2, "name": "Second"},
    }

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.TRANSCODING)


# ── POST /api/jobs/{job_id}/keep-title/{title_id} ─────────────────────────────


@pytest.mark.asyncio
async def test_keep_title_404_job_not_found(api_client):
    response = await api_client.post("/api/jobs/99999/keep-title/0")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_keep_title_409_wrong_status(api_client, db_factory):
    """Job not in AWAITING_TITLE_SELECTION (e.g. still RIPPING) is rejected."""
    job_id = await _create_job(
        db_factory, status=JobStatus.RIPPING, titles_json=_TWO_TITLES, error_message=None
    )

    response = await api_client.post(f"/api/jobs/{job_id}/keep-title/0")

    assert response.status_code == 409
    assert "awaiting title selection" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.RIPPING
    assert job.selected_title_id is None


@pytest.mark.asyncio
async def test_keep_title_409_complete_status(api_client, db_factory):
    """Job already COMPLETE is rejected too."""
    job_id = await _create_job(
        db_factory, status=JobStatus.COMPLETE, titles_json=_TWO_TITLES, error_message=None
    )

    response = await api_client.post(f"/api/jobs/{job_id}/keep-title/0")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_keep_title_400_unknown_title_id(api_client, db_factory):
    """title_id not present among parsed_titles → 400."""
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_TITLE_SELECTION,
        titles_json=_TWO_TITLES,
        error_message=None,
    )

    response = await api_client.post(f"/api/jobs/{job_id}/keep-title/99")

    assert response.status_code == 400
    assert "unknown title_id" in response.json()["detail"].lower()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_TITLE_SELECTION
    assert job.selected_title_id is None


@pytest.mark.asyncio
async def test_keep_title_202_happy_path(api_client, db_factory, mock_queue):
    """Valid title_id among parsed_titles:
    - 202 response
    - DB: selected_title_id set, status=TRANSCODING, progress=0, error_message=None
    - Queue receives (job_id, JobStatus.TRANSCODING)
    """
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_TITLE_SELECTION,
        titles_json=_TWO_TITLES,
        error_message="stale error",
        progress=10,
    )

    response = await api_client.post(f"/api/jobs/{job_id}/keep-title/1")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["title_id"] == 1

    job = await _get_job(db_factory, job_id)
    assert job.selected_title_id == 1
    assert job.status == JobStatus.TRANSCODING
    assert job.progress == 0
    assert job.error_message is None

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.TRANSCODING)


@pytest.mark.asyncio
async def test_keep_title_deletes_discarded_raw_subdirs(
    api_client, db_factory, tmp_path, monkeypatch
):
    """keep-title must delete the raw/<title_id> subdirectory for every title
    OTHER than the kept one, leaving the kept title's subdir untouched."""
    import jacques.api.routes.jobs as jobs_module

    monkeypatch.setattr(jobs_module.settings, "temp_path", tmp_path)

    three_titles = json.dumps(
        [_title(0, "A"), _title(1, "B"), _title(2, "C")]
    )
    job_id = await _create_job(
        db_factory,
        status=JobStatus.AWAITING_TITLE_SELECTION,
        titles_json=three_titles,
        error_message=None,
    )

    raw_root = tmp_path / str(job_id) / "raw"
    kept_dir = raw_root / "1"
    discarded_dirs = [raw_root / "0", raw_root / "2"]
    for d in [kept_dir, *discarded_dirs]:
        d.mkdir(parents=True)
        (d / "title.mkv").write_bytes(b"raw content")

    response = await api_client.post(f"/api/jobs/{job_id}/keep-title/1")

    assert response.status_code == 202
    assert kept_dir.exists()
    assert (kept_dir / "title.mkv").exists()
    for d in discarded_dirs:
        assert not d.exists()
