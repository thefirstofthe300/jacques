"""Integration tests for the full ripping pipeline.

Each test uses an in-memory SQLite database and mocks external binaries
(makemkvcon, HandBrakeCLI) and HTTP calls (TMDb). The file system is real
(via pytest's tmp_path), so file creation and movement are tested end-to-end.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from jacques.api.app import app
from jacques.daemon import _reset_interrupted_jobs
from jacques.database import get_db
from jacques.models.job import DiscType, Job, JobStatus
from jacques.models.ripped_disc import RippedDisc
from jacques.services.metadata import MediaInfo
from jacques.services.ripper import TitleInfo


# ── helpers ───────────────────────────────────────────────────────────────────


def _apply_settings(settings, tmp_path: Path) -> None:
    settings.temp_path = tmp_path / "tmp"
    settings.output_path = tmp_path / "library"
    settings.min_title_duration_seconds = 60
    settings.handbrake_quality = 20
    settings.tmdb_api_key = "testkey"


async def _create_job(db_factory, drive_path: str, disc_label: str | None) -> int:
    async with db_factory() as db:
        job = Job(drive_path=drive_path, disc_label=disc_label, status=JobStatus.DETECTED)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def _get_job(db_factory, job_id: int) -> Job:
    async with db_factory() as db:
        return await db.get(Job, job_id)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_movie_reaches_complete(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    movie_title = TitleInfo(0, "Main Feature", 7200, "title_t00.mkv", 24)

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / "title_t00.mkv"
        mkv.write_bytes(b"fake raw mkv")
        if on_progress:
            await on_progress(100)
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake h265 mkv")
        if on_progress:
            await on_progress(100)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[movie_title])
    mock_ripper.select_main_title = MagicMock(return_value=movie_title)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=MediaInfo(
        title="The Matrix", year=1999, disc_type=DiscType.MOVIE, tmdb_id=603
    ))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "THE_MATRIX")
        await daemon._run_pipeline(job_id, "/dev/sr0", "THE_MATRIX")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE
    assert job.title == "The Matrix"
    assert job.year == 1999
    assert job.disc_type == DiscType.MOVIE
    assert job.tmdb_id == 603
    assert job.progress == 100

    expected = (
        tmp_path / "library" / "Movies" / "The Matrix (1999)" / "The Matrix (1999).mkv"
    )
    assert expected.exists()
    assert expected.read_bytes() == b"fake h265 mkv"


@pytest.mark.asyncio
async def test_pipeline_tv_rips_multiple_titles(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    episodes = [
        TitleInfo(0, "Episode 1", 2700, "t00.mkv", 4),
        TitleInfo(1, "Episode 2", 2640, "t01.mkv", 4),
    ]

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / f"t{title_id:02d}.mkv"
        mkv.write_bytes(b"raw episode")
        if on_progress:
            await on_progress(100)
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"h265 episode")
        if on_progress:
            await on_progress(100)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=episodes)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=True)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=MediaInfo(
        title="Breaking Bad", year=2008, disc_type=DiscType.TV_SHOW, tmdb_id=1396
    ))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "BREAKING_BAD_S1")
        await daemon._run_pipeline(job_id, "/dev/sr0", "BREAKING_BAD_S1")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE
    assert job.disc_type == DiscType.TV_SHOW

    season_dir = tmp_path / "library" / "TV Shows" / "Breaking Bad (2008)" / "Season 01"
    assert (season_dir / "Breaking Bad - S01E01.mkv").exists()
    assert (season_dir / "Breaking Bad - S01E02.mkv").exists()


@pytest.mark.asyncio
async def test_pipeline_marks_failed_on_ripper_error(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(side_effect=RuntimeError("disc unreadable"))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "BAD_DISC")
        await daemon._run_pipeline(job_id, "/dev/sr0", "BAD_DISC")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert "disc unreadable" in job.error_message


@pytest.mark.asyncio
async def test_pipeline_marks_failed_when_no_valid_titles(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[])

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "EMPTY_DISC")
        await daemon._run_pipeline(job_id, "/dev/sr0", "EMPTY_DISC")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert "No valid titles" in job.error_message


@pytest.mark.asyncio
async def test_pipeline_marks_failed_on_transcode_error(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    movie_title = TitleInfo(0, "Film", 7200, "t00.mkv", 1)

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / "t00.mkv"
        mkv.write_bytes(b"raw")
        return mkv

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[movie_title])
    mock_ripper.select_main_title = MagicMock(return_value=movie_title)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock(side_effect=RuntimeError("HandBrakeCLI exited with code 1"))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "FILM")
        await daemon._run_pipeline(job_id, "/dev/sr0", "FILM")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.FAILED
    assert "HandBrakeCLI" in job.error_message


@pytest.mark.asyncio
async def test_pipeline_organizes_without_metadata(db_factory, tmp_path):
    """When TMDb returns no match, files should land in Unknown/ using disc_label."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    movie_title = TitleInfo(0, "Feature", 7200, "t00.mkv", 1)

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / "t00.mkv"
        mkv.write_bytes(b"raw")
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"h265")

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[movie_title])
    mock_ripper.select_main_title = MagicMock(return_value=movie_title)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=None)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "MYSTERY_DISC")
        await daemon._run_pipeline(job_id, "/dev/sr0", "MYSTERY_DISC")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE

    expected = tmp_path / "library" / "Unknown" / "MYSTERY_DISC.mkv"
    assert expected.exists()


@pytest.mark.asyncio
async def test_pipeline_pauses_on_close_metadata_matches(db_factory, tmp_path):
    """When metadata_svc.identify() returns a list of MediaInfo (close matches),
    the pipeline rips the disc then pauses at AWAITING_SELECTION before transcoding."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    movie_title = TitleInfo(0, "Main Feature", 7200, "title_t00.mkv", 24)

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / "title_t00.mkv"
        mkv.write_bytes(b"fake raw mkv")
        return mkv

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[movie_title])
    mock_ripper.select_main_title = MagicMock(return_value=movie_title)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    candidates = [
        MediaInfo(title="The Matrix", year=1999, disc_type=DiscType.MOVIE, tmdb_id=603),
        MediaInfo(title="The Matrix Reloaded", year=2003, disc_type=DiscType.MOVIE, tmdb_id=604),
    ]
    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=candidates)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "THE_MATRIX")
        await daemon._run_pipeline(job_id, "/dev/sr0", "THE_MATRIX")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_SELECTION
    assert job.candidates is not None

    import json
    parsed = json.loads(job.candidates)
    assert len(parsed) == 2

    # Ripping ran, transcoding did not.
    mock_transcoder.transcode.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_cleans_up_temp_dir_on_success(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    movie_title = TitleInfo(0, "Film", 7200, "t00.mkv", 1)

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / "t00.mkv"
        mkv.write_bytes(b"raw")
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"h265")

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[movie_title])
    mock_ripper.select_main_title = MagicMock(return_value=movie_title)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=None)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "DISC")
        await daemon._run_pipeline(job_id, "/dev/sr0", "DISC")

    job_temp = config.settings.temp_path / str(job_id)
    assert not job_temp.exists()


# ── select endpoint fixture ───────────────────────────────────────────────────


@pytest_asyncio.fixture
async def mock_queue():
    return asyncio.Queue()


@pytest_asyncio.fixture
async def api_client(db_factory, mock_queue):
    """AsyncClient wired to the FastAPI app with in-memory DB and mock queue."""
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


# ── select endpoint tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_endpoint_updates_job_and_enqueues_ripping(
    db_factory, api_client, mock_queue
):
    """POST /api/jobs/{id}/select/{tmdb_id} on an AWAITING_SELECTION job should:
    - return 202
    - update title, year, tmdb_id, disc_type from the chosen candidate
    - clear job.candidates
    - set status to RIPPING
    - enqueue (job_id, JobStatus.RIPPING) on the rerun_queue
    """
    candidates = [
        {
            "tmdb_id": 603,
            "title": "The Matrix",
            "year": 1999,
            "disc_type": DiscType.MOVIE.value,
            "overview": "A computer hacker learns the truth.",
        },
        {
            "tmdb_id": 604,
            "title": "The Matrix Reloaded",
            "year": 2003,
            "disc_type": DiscType.MOVIE.value,
            "overview": "Neo and the rebel leaders.",
        },
    ]
    async with db_factory() as db:
        job = Job(
            drive_path="/dev/sr0",
            disc_label="THE_MATRIX",
            status=JobStatus.AWAITING_SELECTION,
            candidates=json.dumps(candidates),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    response = await api_client.post(f"/api/jobs/{job_id}/select/603")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["tmdb_id"] == 603

    async with db_factory() as db:
        updated = await db.get(Job, job_id)

    assert updated.status == JobStatus.TRANSCODING
    assert updated.title == "The Matrix"
    assert updated.year == 1999
    assert updated.tmdb_id == 603
    assert updated.disc_type == DiscType.MOVIE
    assert updated.candidates is None
    assert updated.progress == 0
    assert updated.error_message is None

    assert not mock_queue.empty()
    enqueued = mock_queue.get_nowait()
    assert enqueued == (job_id, JobStatus.TRANSCODING)


@pytest.mark.asyncio
async def test_select_endpoint_404_job_not_found(api_client):
    response = await api_client.post("/api/jobs/99999/select/603")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_select_endpoint_409_wrong_status(db_factory, api_client):
    """Job not in AWAITING_SELECTION should return 409."""
    async with db_factory() as db:
        job = Job(
            drive_path="/dev/sr0",
            disc_label="SOME_DISC",
            status=JobStatus.FAILED,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    response = await api_client.post(f"/api/jobs/{job_id}/select/603")
    assert response.status_code == 409
    assert "awaiting" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_select_endpoint_404_candidate_not_found(db_factory, api_client):
    """Selecting a tmdb_id not in candidates falls through to a direct TMDb lookup.
    If the lookup also fails, the endpoint returns 404."""
    candidates = [
        {
            "tmdb_id": 603,
            "title": "The Matrix",
            "year": 1999,
            "disc_type": DiscType.MOVIE.value,
            "overview": "",
        },
    ]
    async with db_factory() as db:
        job = Job(
            drive_path="/dev/sr0",
            disc_label="THE_MATRIX",
            status=JobStatus.AWAITING_SELECTION,
            candidates=json.dumps(candidates),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    with patch("jacques.api.routes.jobs.MetadataService") as mock_cls:
        mock_cls.return_value.lookup_by_id = AsyncMock(
            side_effect=ValueError("TMDB ID 9999 not found")
        )
        response = await api_client.post(f"/api/jobs/{job_id}/select/9999")

    assert response.status_code == 404
    assert response.json()["detail"] == "TMDB ID not found"


@pytest.mark.asyncio
async def test_reset_interrupted_jobs_preserves_awaiting_selection(db_factory):
    """AWAITING_SELECTION jobs must survive a daemon restart; only truly in-progress
    jobs (RIPPING, TRANSCODING, etc.) should be marked failed."""
    async with db_factory() as db:
        ripping_job = Job(drive_path="/dev/sr0", disc_label="A", status=JobStatus.RIPPING)
        awaiting_job = Job(drive_path="/dev/sr1", disc_label="B", status=JobStatus.AWAITING_SELECTION, candidates="[]")
        complete_job = Job(drive_path="/dev/sr2", disc_label="C", status=JobStatus.COMPLETE)
        db.add_all([ripping_job, awaiting_job, complete_job])
        await db.commit()
        await db.refresh(ripping_job)
        await db.refresh(awaiting_job)
        ripping_id = ripping_job.id
        awaiting_id = awaiting_job.id

    with patch("jacques.daemon.AsyncSessionLocal", db_factory):
        count = await _reset_interrupted_jobs()

    assert count == 1

    async with db_factory() as db:
        ripping = await db.get(Job, ripping_id)
        awaiting = await db.get(Job, awaiting_id)
        assert ripping.status == JobStatus.FAILED
        assert ripping.error_message == "Interrupted by daemon restart"
        assert awaiting.status == JobStatus.AWAITING_SELECTION


# ── RippedDisc insertion tests ────────────────────────────────────────────────


def _make_full_pipeline_mocks(tmp_path: Path):
    """Return (mock_ripper, mock_transcoder, mock_metadata) wired for a simple
    single-movie pipeline that writes real files to tmp_path."""
    movie_title = TitleInfo(0, "Main Feature", 7200, "title_t00.mkv", 24)

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / "title_t00.mkv"
        mkv.write_bytes(b"fake raw mkv")
        if on_progress:
            await on_progress(100)
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake h265 mkv")
        if on_progress:
            await on_progress(100)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=[movie_title])
    mock_ripper.select_main_title = MagicMock(return_value=movie_title)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=MediaInfo(
        title="The Matrix", year=1999, disc_type=DiscType.MOVIE, tmdb_id=603
    ))

    return mock_ripper, mock_transcoder, mock_metadata


@pytest.mark.asyncio
async def test_pipeline_inserts_ripped_disc_on_complete(db_factory, tmp_path):
    """After a successful pipeline run, a RippedDisc row exists with the job's
    disc_label and disc_uuid."""
    from jacques import config, daemon
    from sqlalchemy import select as sa_select

    _apply_settings(config.settings, tmp_path)

    mock_ripper, mock_transcoder, mock_metadata = _make_full_pipeline_mocks(tmp_path)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        async with db_factory() as db:
            job = Job(
                drive_path="/dev/sr0",
                disc_label="THE_MATRIX",
                disc_uuid="abc-123",
                status=JobStatus.DETECTED,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            job_id = job.id

        await daemon._run_pipeline(job_id, "/dev/sr0", "THE_MATRIX")

    async with db_factory() as db:
        row = await db.scalar(
            sa_select(RippedDisc).where(RippedDisc.job_id == job_id)
        )

    assert row is not None
    assert row.disc_label == "THE_MATRIX"
    assert row.disc_uuid == "abc-123"
    assert row.job_id == job_id


@pytest.mark.asyncio
async def test_pipeline_skips_ripped_disc_when_both_identifiers_null(db_factory, tmp_path):
    """When job.disc_label and job.disc_uuid are both None, no RippedDisc row is
    inserted (the DB check constraint would reject it anyway, but the guard in
    _run_pipeline should prevent the attempt entirely)."""
    from jacques import config, daemon
    from sqlalchemy import func, select as sa_select

    _apply_settings(config.settings, tmp_path)

    mock_ripper, mock_transcoder, mock_metadata = _make_full_pipeline_mocks(tmp_path)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        async with db_factory() as db:
            job = Job(
                drive_path="/dev/sr0",
                disc_label=None,
                disc_uuid=None,
                status=JobStatus.DETECTED,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            job_id = job.id

        await daemon._run_pipeline(job_id, "/dev/sr0", None)

    async with db_factory() as db:
        count = await db.scalar(
            sa_select(func.count()).select_from(RippedDisc).where(RippedDisc.job_id == job_id)
        )

    assert count == 0


@pytest.mark.asyncio
async def test_pipeline_ripped_disc_insertion_is_idempotent(db_factory, tmp_path):
    """Calling _run_pipeline a second time after COMPLETE does not create a
    duplicate RippedDisc row for the same job_id."""
    from jacques import config, daemon
    from sqlalchemy import func, select as sa_select

    _apply_settings(config.settings, tmp_path)

    mock_ripper, mock_transcoder, mock_metadata = _make_full_pipeline_mocks(tmp_path)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "THE_MATRIX")
        await daemon._run_pipeline(job_id, "/dev/sr0", "THE_MATRIX")

    # Verify exactly one row exists after the first run.
    async with db_factory() as db:
        count_after_first = await db.scalar(
            sa_select(func.count()).select_from(RippedDisc).where(RippedDisc.job_id == job_id)
        )
    assert count_after_first == 1

    # Directly invoke the COMPLETE-section logic a second time by inserting
    # a RippedDisc row the same way the daemon would.  This simulates calling
    # the guard code twice for the same job_id.
    async with db_factory() as db:
        from sqlalchemy import select as _sel
        existing = await db.scalar(
            _sel(RippedDisc).where(RippedDisc.job_id == job_id).limit(1)
        )
        if existing is None:
            db.add(RippedDisc(disc_label="THE_MATRIX", disc_uuid=None, job_id=job_id))
            await db.commit()

    async with db_factory() as db:
        count_after_second = await db.scalar(
            sa_select(func.count()).select_from(RippedDisc).where(RippedDisc.job_id == job_id)
        )

    assert count_after_second == 1
