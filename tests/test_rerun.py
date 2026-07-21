"""Tests for _should_run() and _run_pipeline() start_stage / rerun behaviour.

These tests exercise the three new elements added in the rerun refactor:
  - _RERUN_STAGES  (module-level list)
  - _should_run()  (pure function, unit-tested exhaustively)
  - _run_pipeline(start_stage=…) integration paths for FETCHING_METADATA,
    TRANSCODING, and ORGANIZING entry points.

Each integration test uses the same in-memory SQLite db_factory fixture as
test_pipeline.py and monkeypatches jacques.daemon.settings.temp_path so that
temp-file probing goes to pytest's tmp_path instead of the real filesystem.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jacques.models.job import DiscType, Job, JobStatus
from jacques.services.metadata import MediaInfo


# ── helpers (mirrors test_pipeline.py) ────────────────────────────────────────


def _apply_settings(settings, tmp_path: Path) -> None:
    settings.temp_path = tmp_path / "tmp"
    settings.output_path = tmp_path / "library"
    settings.min_title_duration_seconds = 60
    settings.handbrake_quality = 20
    settings.tmdb_api_key = "testkey"


async def _create_job(
    db_factory,
    drive_path: str,
    disc_label: str | None,
    *,
    status: JobStatus = JobStatus.DETECTED,
    disc_type: DiscType = DiscType.UNKNOWN,
    title: str | None = None,
    year: int | None = None,
    tmdb_id: int | None = None,
) -> int:
    async with db_factory() as db:
        job = Job(
            drive_path=drive_path,
            disc_label=disc_label,
            status=status,
            disc_type=disc_type,
            title=title,
            year=year,
            tmdb_id=tmdb_id,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def _get_job(db_factory, job_id: int) -> Job:
    async with db_factory() as db:
        return await db.get(Job, job_id)


# ── _should_run() unit tests ───────────────────────────────────────────────────


class TestShouldRun:
    """Pure unit tests — no DB, no filesystem."""

    def test_same_stage_returns_true(self):
        from jacques.daemon import _should_run

        for stage in [
            JobStatus.IDENTIFYING,
            JobStatus.FETCHING_METADATA,
            JobStatus.RIPPING,
            JobStatus.TRANSCODING,
            JobStatus.ORGANIZING,
        ]:
            assert _should_run(stage, stage) is True, f"same stage {stage} should return True"

    def test_stage_after_start_returns_true(self):
        from jacques.daemon import _should_run

        # RIPPING is after IDENTIFYING
        assert _should_run(JobStatus.RIPPING, JobStatus.IDENTIFYING) is True
        # ORGANIZING is after FETCHING_METADATA
        assert _should_run(JobStatus.ORGANIZING, JobStatus.FETCHING_METADATA) is True
        # TRANSCODING is after RIPPING
        assert _should_run(JobStatus.TRANSCODING, JobStatus.RIPPING) is True

    def test_stage_before_start_returns_false(self):
        from jacques.daemon import _should_run

        # IDENTIFYING is before FETCHING_METADATA
        assert _should_run(JobStatus.IDENTIFYING, JobStatus.FETCHING_METADATA) is False
        # IDENTIFYING is before TRANSCODING
        assert _should_run(JobStatus.IDENTIFYING, JobStatus.TRANSCODING) is False
        # FETCHING_METADATA is before RIPPING
        assert _should_run(JobStatus.FETCHING_METADATA, JobStatus.RIPPING) is False
        # RIPPING is before ORGANIZING
        assert _should_run(JobStatus.RIPPING, JobStatus.ORGANIZING) is False

    def test_stage_not_in_rerun_stages_returns_true(self):
        """Stages outside _RERUN_STAGES (e.g. COMPLETE, FAILED, DETECTED) trigger
        the ValueError fallback and must return True."""
        from jacques.daemon import _should_run

        for stage in [JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.DETECTED]:
            assert _should_run(stage, JobStatus.IDENTIFYING) is True, (
                f"stage {stage} not in _RERUN_STAGES should return True"
            )

    def test_start_stage_not_in_rerun_stages_returns_true(self):
        """If start_stage itself is outside _RERUN_STAGES the fallback fires."""
        from jacques.daemon import _should_run

        assert _should_run(JobStatus.IDENTIFYING, JobStatus.COMPLETE) is True

    def test_full_ordering(self):
        """Verify the complete ordering implied by _RERUN_STAGES."""
        from jacques.daemon import _RERUN_STAGES, _should_run

        for i, stage in enumerate(_RERUN_STAGES):
            for j, start in enumerate(_RERUN_STAGES):
                expected = i >= j
                assert _should_run(stage, start) is expected, (
                    f"_should_run({stage}, {start}) expected {expected}"
                )


# ── _run_pipeline integration tests — start_stage=FETCHING_METADATA ───────────


@pytest.mark.asyncio
async def test_rerun_from_fetching_metadata_skips_disc_info(db_factory, tmp_path):
    """When start_stage=FETCHING_METADATA:
    - disc_type_hint is loaded from the DB (IDENTIFYING is skipped)
    - metadata_svc.identify is called
    - ripper.get_disc_info() is NOT called
    - ripper.rip() is NOT called (no titles_to_rip from the skipped IDENTIFYING stage)
    - job ends COMPLETE
    """
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(
        return_value=MediaInfo(
            title="Inception", year=2010, disc_type=DiscType.MOVIE, tmdb_id=27205
        )
    )

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(
            db_factory,
            "/dev/sr0",
            "INCEPTION",
            disc_type=DiscType.MOVIE,
        )
        await daemon._run_pipeline(
            job_id, "/dev/sr0", "INCEPTION", start_stage=JobStatus.FETCHING_METADATA
        )

    mock_ripper.get_disc_info.assert_not_called()
    mock_ripper.rip.assert_not_called()
    mock_metadata.identify.assert_called_once()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE
    assert job.title == "Inception"
    assert job.year == 2010


@pytest.mark.asyncio
async def test_rerun_from_fetching_metadata_loads_disc_type_from_db(db_factory, tmp_path):
    """disc_type_hint should be taken from the stored job.disc_type when
    start_stage=FETCHING_METADATA, and passed through to metadata_svc.identify."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    captured_calls: list = []

    async def spy_identify(label, disc_type):
        captured_calls.append((label, disc_type))
        return None  # no metadata match — pauses at AWAITING_SELECTION

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = spy_identify

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(
            db_factory,
            "/dev/sr0",
            "SHOW_DISC",
            disc_type=DiscType.TV_SHOW,  # pre-stored by the original IDENTIFYING run
        )
        await daemon._run_pipeline(
            job_id, "/dev/sr0", "SHOW_DISC", start_stage=JobStatus.FETCHING_METADATA
        )

    assert len(captured_calls) == 1
    assert captured_calls[0] == ("SHOW_DISC", DiscType.TV_SHOW)

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_SELECTION
    assert json.loads(job.candidates) == []


# ── _run_pipeline integration tests — start_stage=TRANSCODING ─────────────────


@pytest.mark.asyncio
async def test_rerun_from_transcoding_uses_existing_raw_files(db_factory, tmp_path):
    """When start_stage=TRANSCODING:
    - raw_paths are loaded from <temp>/<job_id>/raw/ (given .done marker + .mkv files)
    - ripper.get_disc_info() is NOT called
    - ripper.rip() is NOT called
    - transcoder.transcode() IS called with the loaded raw file(s)
    - job ends COMPLETE
    """
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    # Pre-create the raw output that a previous RIPPING run would have left
    job_id_placeholder = 999  # we'll get the real ID after creating the job
    # Create the job first so we know the ID
    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=MagicMock()),
        patch("jacques.daemon.Transcoder", return_value=MagicMock()),
        patch("jacques.daemon.MetadataService", return_value=MagicMock()),
    ):
        job_id = await _create_job(
            db_factory,
            "/dev/sr0",
            "RAW_MOVIE",
            disc_type=DiscType.MOVIE,
            title="Raw Movie",
            year=2020,
            tmdb_id=42,
        )

    # Now set up the raw directory with a .done marker. Raw files live in a
    # per-title_id subdirectory (raw_dir/{title_id}/*.mkv).
    raw_dir = config.settings.temp_path / str(job_id) / "raw"
    (raw_dir / "0").mkdir(parents=True)
    raw_file = raw_dir / "0" / "title_t00.mkv"
    raw_file.write_bytes(b"raw ripped content")
    (raw_dir / ".done").touch()

    transcoded_calls: list = []

    async def fake_transcode(input_path, output_path, on_progress=None):
        transcoded_calls.append((input_path, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"transcoded content")
        if on_progress:
            await on_progress(100)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = fake_transcode

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(
        return_value=MediaInfo(
            title="Raw Movie", year=2020, disc_type=DiscType.MOVIE, tmdb_id=42
        )
    )

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        await daemon._run_pipeline(
            job_id, "/dev/sr0", "RAW_MOVIE", start_stage=JobStatus.TRANSCODING
        )

    mock_ripper.get_disc_info.assert_not_called()
    mock_ripper.rip.assert_not_called()

    assert len(transcoded_calls) == 1
    assert transcoded_calls[0][0] == raw_file

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE


@pytest.mark.asyncio
async def test_rerun_from_transcoding_no_raw_done_marker_skips_transcode(db_factory, tmp_path):
    """If the .done marker is absent in raw/, raw_paths stays empty and
    transcode is never called — job still reaches COMPLETE (organizing no files)."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=None)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "MISSING_RAW")

        # raw dir exists but NO .done marker
        raw_dir = config.settings.temp_path / str(job_id) / "raw"
        (raw_dir / "0").mkdir(parents=True)
        (raw_dir / "0" / "title_t00.mkv").write_bytes(b"partial rip")

        await daemon._run_pipeline(
            job_id, "/dev/sr0", "MISSING_RAW", start_stage=JobStatus.TRANSCODING
        )

    mock_transcoder.transcode.assert_not_called()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE


# ── _run_pipeline integration tests — start_stage=ORGANIZING ──────────────────


@pytest.mark.asyncio
async def test_rerun_from_organizing_uses_existing_transcoded_files(db_factory, tmp_path):
    """When start_stage=ORGANIZING:
    - transcoded_paths are loaded from <temp>/<job_id>/transcoded/ (given .done + .mkv)
    - ripper.get_disc_info() is NOT called
    - ripper.rip() is NOT called
    - transcoder.transcode() is NOT called
    - organizer.move() IS called — file lands in the output library
    - job ends COMPLETE
    """
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(
        return_value=MediaInfo(
            title="Dune", year=2021, disc_type=DiscType.MOVIE, tmdb_id=438631
        )
    )

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(
            db_factory,
            "/dev/sr0",
            "DUNE",
            disc_type=DiscType.MOVIE,
            title="Dune",
            year=2021,
            tmdb_id=438631,
        )

    # Set up the transcoded directory. Transcoded files live in a per-title_id
    # subdirectory (transcoded_dir/{title_id}/*.mkv).
    transcoded_dir = config.settings.temp_path / str(job_id) / "transcoded"
    (transcoded_dir / "0").mkdir(parents=True)
    transcoded_file = transcoded_dir / "0" / "dune.mkv"
    transcoded_file.write_bytes(b"h265 transcoded dune")
    (transcoded_dir / ".done").touch()

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        await daemon._run_pipeline(
            job_id, "/dev/sr0", "DUNE", start_stage=JobStatus.ORGANIZING
        )

    mock_ripper.get_disc_info.assert_not_called()
    mock_ripper.rip.assert_not_called()
    mock_transcoder.transcode.assert_not_called()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE

    # The file should be in the library
    expected = tmp_path / "library" / "Movies" / "Dune (2021)" / "Dune (2021).mkv"
    assert expected.exists()
    assert expected.read_bytes() == b"h265 transcoded dune"


@pytest.mark.asyncio
async def test_rerun_from_organizing_no_transcoded_done_marker_completes_empty(db_factory, tmp_path):
    """If transcoded/.done is absent, transcoded_paths stays empty and organize
    is a no-op — job still reaches COMPLETE."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=None)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(db_factory, "/dev/sr0", "MISSING_TRANSCODED")

        # transcoded dir exists but NO .done marker
        transcoded_dir = config.settings.temp_path / str(job_id) / "transcoded"
        (transcoded_dir / "0").mkdir(parents=True)
        (transcoded_dir / "0" / "film.mkv").write_bytes(b"incomplete transcode")

        await daemon._run_pipeline(
            job_id, "/dev/sr0", "MISSING_TRANSCODED", start_stage=JobStatus.ORGANIZING
        )

    mock_transcoder.transcode.assert_not_called()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE


@pytest.mark.asyncio
async def test_rerun_from_fetching_metadata_with_raw_files_does_not_transcode(db_factory, tmp_path):
    """With start_stage=FETCHING_METADATA, raw_paths are NOT pre-loaded from disk
    (only TRANSCODING/ORGANIZING entry points pre-load them). Even if raw files exist,
    transcode is never called — ripping produces nothing (no titles_to_rip from
    the skipped IDENTIFYING stage), and since TMDb finds no match here either, the
    pipeline pauses at AWAITING_SELECTION rather than reaching COMPLETE."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=None)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(
            db_factory, "/dev/sr0", "RAW_EXISTS", disc_type=DiscType.MOVIE
        )
        raw_dir = config.settings.temp_path / str(job_id) / "raw"
        (raw_dir / "0").mkdir(parents=True)
        (raw_dir / "0" / "title_t00.mkv").write_bytes(b"raw content")
        (raw_dir / ".done").touch()

        await daemon._run_pipeline(
            job_id, "/dev/sr0", "RAW_EXISTS", start_stage=JobStatus.FETCHING_METADATA
        )

    mock_ripper.rip.assert_not_called()
    mock_transcoder.transcode.assert_not_called()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.AWAITING_SELECTION
    assert json.loads(job.candidates) == []


# ── _process_reruns() tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_reruns_dispatches_pipeline(db_factory, tmp_path):
    """_process_reruns() dequeues a (job_id, start_stage) tuple and spawns _run_pipeline."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    pipeline_calls: list = []

    async def fake_pipeline(job_id, drive_path, disc_label, disc_uuid, start_stage):
        pipeline_calls.append((job_id, start_stage))

    job_id = await _create_job(
        db_factory, "/dev/sr0", "TEST_DISC", disc_type=DiscType.MOVIE
    )

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put((job_id, JobStatus.TRANSCODING))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon._run_pipeline", AsyncMock(side_effect=fake_pipeline)),
    ):
        consumer = asyncio.create_task(daemon._process_reruns(queue, set()))
        await queue.join()
        await asyncio.sleep(0)  # let the spawned pipeline task run
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert len(pipeline_calls) == 1
    assert pipeline_calls[0] == (job_id, JobStatus.TRANSCODING)


@pytest.mark.asyncio
async def test_process_reruns_skips_unknown_job(db_factory, tmp_path):
    """_process_reruns() warns and skips when the job_id is not found in DB."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    pipeline_calls: list = []

    async def fake_pipeline(*args, **kwargs):
        pipeline_calls.append(args)

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put((99999, JobStatus.TRANSCODING))

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon._run_pipeline", AsyncMock(side_effect=fake_pipeline)),
    ):
        consumer = asyncio.create_task(daemon._process_reruns(queue, set()))
        await queue.join()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert pipeline_calls == []


@pytest.mark.asyncio
async def test_process_reruns_cancels_cleanly():
    """_process_reruns() exits without error when its task is cancelled."""
    from jacques import daemon

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(daemon._process_reruns(queue, set()))
    await asyncio.sleep(0)  # let the task start and block on queue.get()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.done()


@pytest.mark.asyncio
async def test_rerun_from_organizing_does_not_call_metadata_identify(db_factory, tmp_path):
    """When start_stage=ORGANIZING, FETCHING_METADATA is skipped entirely so
    metadata_svc.identify should never be called."""
    from jacques import config, daemon

    _apply_settings(config.settings, tmp_path)

    mock_ripper = MagicMock()
    mock_ripper.get_disc_info = AsyncMock()
    mock_ripper.rip = AsyncMock()

    mock_transcoder = MagicMock()
    mock_transcoder.transcode = AsyncMock()

    mock_metadata = MagicMock()
    mock_metadata.identify = AsyncMock(return_value=None)

    with (
        patch("jacques.daemon.AsyncSessionLocal", db_factory),
        patch("jacques.daemon.Ripper", return_value=mock_ripper),
        patch("jacques.daemon.Transcoder", return_value=mock_transcoder),
        patch("jacques.daemon.MetadataService", return_value=mock_metadata),
    ):
        job_id = await _create_job(
            db_factory,
            "/dev/sr0",
            "SKIP_META",
            disc_type=DiscType.MOVIE,
            title="Already Named",
            year=2000,
        )
        await daemon._run_pipeline(
            job_id, "/dev/sr0", "SKIP_META", start_stage=JobStatus.ORGANIZING
        )

    mock_metadata.identify.assert_not_called()

    job = await _get_job(db_factory, job_id)
    assert job.status == JobStatus.COMPLETE
