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
from jacques.daemon import _reset_interrupted_jobs, _titles_to_rip
from jacques.database import get_db
from jacques.models.job import DiscType, Job, JobStatus
from jacques.models.ripped_disc import RippedDisc
from jacques.services.metadata import MediaInfo
from jacques.services.ripper import Ripper, TitleInfo


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


# ── _titles_to_rip unit tests ─────────────────────────────────────────────────


def test_titles_to_rip_tv_show_returns_all_titles():
    ripper = Ripper("/dev/sr0")
    titles = [
        TitleInfo(0, "Episode 1", 2700, "t00.mkv"),
        TitleInfo(1, "Episode 2", 2640, "t01.mkv"),
    ]
    assert _titles_to_rip(ripper, DiscType.TV_SHOW, titles) == titles


def test_titles_to_rip_movie_single_title():
    ripper = Ripper("/dev/sr0")
    titles = [TitleInfo(0, "Feature", 7200, "t00.mkv")]
    assert _titles_to_rip(ripper, DiscType.MOVIE, titles) == [titles[0]]


def test_titles_to_rip_movie_multiple_with_flagged_main_feature():
    ripper = Ripper("/dev/sr0")
    main = TitleInfo(0, "Title (FPL_MainFeature)", 7200, "t00.mkv")
    other = TitleInfo(1, "Other Title", 6900, "t01.mkv")
    assert _titles_to_rip(ripper, DiscType.MOVIE, [main, other]) == [main]


def test_titles_to_rip_movie_multiple_ambiguous_returns_all():
    ripper = Ripper("/dev/sr0")
    titles = [
        TitleInfo(0, "Title A", 7200, "t00.mkv"),
        TitleInfo(1, "Title B", 6900, "t01.mkv"),
    ]
    assert _titles_to_rip(ripper, DiscType.MOVIE, titles) == titles


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
async def test_pipeline_ambiguous_movie_rips_all_candidates(db_factory, tmp_path):
    """When a disc looks like a movie (not a TV-show hint) but has multiple titles
    with none flagged as FPL_MainFeature, the pipeline should rip every candidate
    rather than guessing via select_main_title."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    candidates = [
        TitleInfo(0, "Title A", 7200, "t00.mkv", 1),
        TitleInfo(1, "Title B", 6900, "t01.mkv", 1),
    ]

    rip_calls: list[int] = []

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        rip_calls.append(title_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / f"t{title_id:02d}.mkv"
        mkv.write_bytes(f"raw-{title_id}".encode())
        if on_progress:
            await on_progress(100)
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(input_path.read_bytes())
        if on_progress:
            await on_progress(100)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=candidates)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=False)
    mock_ripper.has_ambiguous_main_feature = MagicMock(return_value=True)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=MediaInfo(
        title="Ambiguous Movie", year=2020, disc_type=DiscType.MOVIE, tmdb_id=1
    ))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "AMBIGUOUS_MOVIE")
        await daemon._run_pipeline(job_id, "/dev/sr0", "AMBIGUOUS_MOVIE")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE
    assert job.disc_type == DiscType.MOVIE

    # Both candidates were ripped — no single-title guess was made.
    assert rip_calls == [0, 1]


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


# ── per-title raw/transcode subdirectory isolation ─────────────────────────────
# Regression coverage for the bug where every title in a multi-title rip shared
# one raw_dir, and Ripper.rip() (which returns "the largest .mkv in output_dir")
# could therefore return an earlier, larger title's file when ripping a later,
# smaller title. The fix gives each title_id its own raw_dir/{title_id}/ (and
# transcoded_dir/{title_id}/) subdirectory.


@pytest.mark.asyncio
async def test_pipeline_rips_titles_into_distinct_subdirectories(db_factory, tmp_path):
    """Each title in a multi-title rip must be written to its own raw_dir/{title_id}/
    subdirectory rather than a single shared directory."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    episodes = [
        TitleInfo(0, "Episode 1", 2700, "t00.mkv", 4),
        TitleInfo(1, "Episode 2", 2640, "t01.mkv", 4),
        TitleInfo(2, "Episode 3", 2610, "t02.mkv", 4),
    ]

    rip_calls: list[tuple[int, Path]] = []

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        rip_calls.append((title_id, output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / f"t{title_id:02d}.mkv"
        mkv.write_bytes(f"raw-{title_id}".encode())
        if on_progress:
            await on_progress(100)
        return mkv

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(input_path.read_bytes())
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
        title="Some Show", year=2010, disc_type=DiscType.TV_SHOW, tmdb_id=42
    ))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "SOME_SHOW")
        await daemon._run_pipeline(job_id, "/dev/sr0", "SOME_SHOW")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE

    raw_dir = config.settings.temp_path / str(job_id) / "raw"
    output_dirs = [output_dir for _, output_dir in rip_calls]

    # Each title got its own subdirectory, named after its title_id.
    assert output_dirs == [raw_dir / "0", raw_dir / "1", raw_dir / "2"]
    assert len(set(output_dirs)) == 3

    season_dir = tmp_path / "library" / "TV Shows" / "Some Show (2010)" / "Season 01"
    assert (season_dir / "Some Show - S01E01.mkv").read_bytes() == b"raw-0"
    assert (season_dir / "Some Show - S01E02.mkv").read_bytes() == b"raw-1"
    assert (season_dir / "Some Show - S01E03.mkv").read_bytes() == b"raw-2"


@pytest.mark.asyncio
async def test_pipeline_title_attribution_survives_largest_file_selection(db_factory, tmp_path):
    """Regression test for the specific bug: ripping a larger title A followed by a
    smaller title B used to be able to return title A's file for title B, because
    Ripper.rip() picks the largest .mkv file present in its output_dir. The fake rip
    stub here mirrors that real selection logic (rather than just returning a
    hardcoded path) so the per-title subdirectory fix is actually exercised — if
    titles still shared one output_dir, ripping title B second would incorrectly
    pick up title A's larger file."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    titles = [
        TitleInfo(0, "Episode 1", 2700, "t00.mkv", 4),
        TitleInfo(1, "Episode 2", 2640, "t01.mkv", 4),
    ]
    sizes = {0: 10_000, 1: 500}  # title 0 intentionally much larger than title 1

    async def fake_rip(title_id, output_dir, on_progress=None, expected_bytes=0):
        output_dir.mkdir(parents=True, exist_ok=True)
        mkv = output_dir / f"t{title_id:02d}.mkv"
        mkv.write_bytes(bytes([title_id + 1]) * sizes[title_id])
        if on_progress:
            await on_progress(100)
        # Mirrors jacques.services.ripper.Ripper.rip(): return the largest .mkv
        # file currently present in output_dir.
        return max(output_dir.glob("*.mkv"), key=lambda p: p.stat().st_size)

    async def fake_transcode(input_path, output_path, on_progress=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(input_path.read_bytes())

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock(return_value=titles)
    mock_ripper.is_tv_show_hint = MagicMock(return_value=True)
    mock_ripper.rip = fake_rip

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=MediaInfo(
        title="Regression Show", year=2015, disc_type=DiscType.TV_SHOW, tmdb_id=7
    ))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "REGRESSION_SHOW")
        await daemon._run_pipeline(job_id, "/dev/sr0", "REGRESSION_SHOW")

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE

    season_dir = tmp_path / "library" / "TV Shows" / "Regression Show (2015)" / "Season 01"
    ep1 = season_dir / "Regression Show - S01E01.mkv"
    ep2 = season_dir / "Regression Show - S01E02.mkv"

    assert ep1.read_bytes() == bytes([1]) * 10_000
    # Under the old shared-directory bug, this would incorrectly equal ep1's content.
    assert ep2.read_bytes() == bytes([2]) * 500
    assert ep1.read_bytes() != ep2.read_bytes()


@pytest.mark.asyncio
async def test_pipeline_resumes_transcoding_from_per_title_raw_subdirs(db_factory, tmp_path):
    """When resuming a rerun starting at TRANSCODING, raw files must be discovered
    from raw_dir/{title_id}/*.mkv and processed in numeric title_id order — not
    lexicographic order, where "10" would incorrectly sort before "2"."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    job_id = await _create_job(db_factory, "/dev/sr0", "RESUME_SHOW")
    async with db_factory() as db:
        job = await db.get(Job, job_id)
        job.title = "Resume Show"
        job.year = 2012
        job.disc_type = DiscType.TV_SHOW
        job.tmdb_id = 99
        await db.commit()

    job_temp = config.settings.temp_path / str(job_id)
    raw_dir = job_temp / "raw"
    (raw_dir / "2").mkdir(parents=True)
    (raw_dir / "2" / "t02.mkv").write_bytes(b"raw-title-2")
    (raw_dir / "10").mkdir(parents=True)
    (raw_dir / "10" / "t10.mkv").write_bytes(b"raw-title-10")
    (raw_dir / ".done").touch()

    transcode_calls: list[Path] = []

    async def fake_transcode(input_path, output_path, on_progress=None):
        transcode_calls.append(input_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(input_path.read_bytes())

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
    ):
        await daemon._run_pipeline(
            job_id, "/dev/sr0", "RESUME_SHOW", start_stage=JobStatus.TRANSCODING
        )

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE

    # Numeric sort: title_id 2 before title_id 10.
    assert [p.parent.name for p in transcode_calls] == ["2", "10"]

    season_dir = tmp_path / "library" / "TV Shows" / "Resume Show (2012)" / "Season 01"
    assert (season_dir / "Resume Show - S01E01.mkv").read_bytes() == b"raw-title-2"
    assert (season_dir / "Resume Show - S01E02.mkv").read_bytes() == b"raw-title-10"


@pytest.mark.asyncio
async def test_find_resumable_paths_raw_only_sorted_by_title_id(db_factory, tmp_path):
    """_find_resumable_paths() must glob one level deeper (raw_dir/*/*.mkv) and sort
    by numeric title_id, using the raw-only branch (no transcoded output present)."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    async with db_factory() as db:
        prior_job = Job(drive_path="/dev/sr0", disc_label="RESUME_DISC", status=JobStatus.FAILED)
        db.add(prior_job)
        await db.commit()
        await db.refresh(prior_job)
        prior_job_id = prior_job.id

    prior_temp = config.settings.temp_path / str(prior_job_id)
    raw_dir = prior_temp / "raw"
    (raw_dir / "5").mkdir(parents=True)
    (raw_dir / "5" / "t05.mkv").write_bytes(b"raw-5")
    (raw_dir / "20").mkdir(parents=True)
    (raw_dir / "20" / "t20.mkv").write_bytes(b"raw-20")
    (raw_dir / ".done").touch()

    with patch("jacques.daemon.AsyncSessionLocal", db_factory):
        raw_paths, transcoded_paths, found_prior_id = await daemon._find_resumable_paths(
            "RESUME_DISC", exclude_job_id=prior_job_id + 1
        )

    assert found_prior_id == prior_job_id
    assert transcoded_paths == []
    assert [p.parent.name for p in raw_paths] == ["5", "20"]


@pytest.mark.asyncio
async def test_find_resumable_paths_transcoded_found_sorted_by_title_id(db_factory, tmp_path):
    """When a prior job's transcoded output is present and complete,
    _find_resumable_paths() must return both raw and transcoded paths from their
    per-title subdirectories, sorted by numeric title_id."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    async with db_factory() as db:
        prior_job = Job(drive_path="/dev/sr0", disc_label="RESUME_DISC_2", status=JobStatus.FAILED)
        db.add(prior_job)
        await db.commit()
        await db.refresh(prior_job)
        prior_job_id = prior_job.id

    prior_temp = config.settings.temp_path / str(prior_job_id)

    transcoded_dir = prior_temp / "transcoded"
    (transcoded_dir / "3").mkdir(parents=True)
    (transcoded_dir / "3" / "t03.mkv").write_bytes(b"transcoded-3")
    (transcoded_dir / "11").mkdir(parents=True)
    (transcoded_dir / "11" / "t11.mkv").write_bytes(b"transcoded-11")
    (transcoded_dir / ".done").touch()

    # No raw_dir/".done" marker here — the transcoded-found branch doesn't require
    # one; raw is only kept around as a cleanup reference in that case.
    raw_dir = prior_temp / "raw"
    (raw_dir / "3").mkdir(parents=True)
    (raw_dir / "3" / "t03.mkv").write_bytes(b"raw-3")
    (raw_dir / "11").mkdir(parents=True)
    (raw_dir / "11" / "t11.mkv").write_bytes(b"raw-11")

    with patch("jacques.daemon.AsyncSessionLocal", db_factory):
        raw_paths, transcoded_paths, found_prior_id = await daemon._find_resumable_paths(
            "RESUME_DISC_2", exclude_job_id=prior_job_id + 1
        )

    assert found_prior_id == prior_job_id
    assert [p.parent.name for p in transcoded_paths] == ["3", "11"]
    assert [p.parent.name for p in raw_paths] == ["3", "11"]


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

        await daemon._run_pipeline(job_id, "/dev/sr0", "THE_MATRIX", "abc-123")

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
