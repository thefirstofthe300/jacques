"""Integration tests for the full ripping pipeline.

Each test uses an in-memory SQLite database and mocks external binaries
(makemkvcon, HandBrakeCLI) and HTTP calls (TMDb). The file system is real
(via pytest's tmp_path), so file creation and movement are tested end-to-end.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jacques.models.job import DiscType, Job, JobStatus
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

    async def fake_rip(title_id, output_dir, on_progress=None):
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

    async def fake_rip(title_id, output_dir, on_progress=None):
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

    async def fake_rip(title_id, output_dir, on_progress=None):
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

    async def fake_rip(title_id, output_dir, on_progress=None):
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
async def test_pipeline_cleans_up_temp_dir_on_success(db_factory, tmp_path):
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    movie_title = TitleInfo(0, "Film", 7200, "t00.mkv", 1)

    async def fake_rip(title_id, output_dir, on_progress=None):
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
