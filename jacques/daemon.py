import asyncio
import dataclasses
import json
import logging
import shutil
from pathlib import Path

import uvicorn
from rich.logging import RichHandler
from sqlalchemy import select

from .api.app import app
from .config import settings
from .database import AsyncSessionLocal, init_db
from .models.job import DiscType, Job, JobStatus
from .services.detector import DiscDetector
from .services.metadata import MediaInfo, MetadataService
from .services.organizer import Organizer
from .services.ripper import Ripper, TitleInfo
from .services.transcoder import Transcoder

log = logging.getLogger(__name__)


async def _update_job(job_id: int, **kwargs: object) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        for key, value in kwargs.items():
            setattr(job, key, value)
        await db.commit()


def _stage_progress(job_id: int, stage_index: int, stage_count: int):
    """Return a progress callback that scales stage 0-100 into overall 0-100."""
    async def _cb(pct: int) -> None:
        overall = (stage_index * 100 + pct) // stage_count
        await _update_job(job_id, progress=overall)
    return _cb


async def _find_resumable_paths(
    disc_label: str | None, exclude_job_id: int
) -> tuple[list[Path], list[Path], int | None]:
    """Return (raw_paths, transcoded_paths, prior_job_id) from the most recent failed
    job with the same disc label that has usable temp files.

    Checks transcoded first (further along the pipeline), then raw.
    All three values are empty/None if nothing is resumable.
    """
    if not disc_label:
        return [], [], None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Job.id)
            .where(Job.disc_label == disc_label, Job.status == JobStatus.FAILED, Job.id != exclude_job_id)
            .order_by(Job.id.desc())
        )
        prior_ids: list[int] = list(result.scalars())

    for prior_id in prior_ids:
        prior_temp = settings.temp_path / str(prior_id)

        transcoded_dir = prior_temp / "transcoded"
        transcoded = sorted(transcoded_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True) if (transcoded_dir.exists() and (transcoded_dir / ".done").exists()) else []
        if transcoded:
            raw_dir = prior_temp / "raw"
            raw = sorted(raw_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True) if raw_dir.exists() else []
            log.info("Job %d: found resumable transcoded output from job %d", exclude_job_id, prior_id)
            return raw, transcoded, prior_id

        raw_dir = prior_temp / "raw"
        raw = sorted(raw_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True) if (raw_dir.exists() and (raw_dir / ".done").exists()) else []
        if raw:
            log.info("Job %d: found resumable raw output from job %d", exclude_job_id, prior_id)
            return raw, [], prior_id

    return [], [], None


_RERUN_STAGES = [
    JobStatus.IDENTIFYING,
    JobStatus.FETCHING_METADATA,
    JobStatus.RIPPING,
    JobStatus.TRANSCODING,
    JobStatus.ORGANIZING,
]


def _should_run(stage: JobStatus, start_stage: JobStatus) -> bool:
    try:
        return _RERUN_STAGES.index(stage) >= _RERUN_STAGES.index(start_stage)
    except ValueError:
        return True


async def _run_pipeline(
    job_id: int,
    drive_path: str,
    disc_label: str | None,
    start_stage: JobStatus = JobStatus.IDENTIFYING,
) -> None:
    job_temp = settings.temp_path / str(job_id)
    raw_dir = job_temp / "raw"
    transcoded_dir = job_temp / "transcoded"

    ripper = Ripper(drive_path, settings.min_title_duration_seconds, settings.makemkvcon_path)
    transcoder = Transcoder(quality=settings.handbrake_quality, handbrake_path=settings.handbrake_path, preset=settings.handbrake_preset)
    metadata_svc = MetadataService(settings.tmdb_api_key)
    organizer = Organizer(settings.output_path)

    disc_type_hint: DiscType = DiscType.UNKNOWN
    titles_to_rip: list = []
    media_info: MediaInfo | None = None
    raw_paths: list[Path] = []
    transcoded_paths: list[Path] = []
    resume_raw: list[Path] = []
    resume_transcoded: list[Path] = []
    prior_job_id: int | None = None

    if not _should_run(JobStatus.IDENTIFYING, start_stage):
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job is not None:
                disc_type_hint = job.disc_type
                if job.titles_json:
                    all_titles = [TitleInfo(**t) for t in json.loads(job.titles_json)]
                    titles_to_rip = (
                        all_titles
                        if disc_type_hint == DiscType.TV_SHOW
                        else [ripper.select_main_title(all_titles)]
                    )
                if not _should_run(JobStatus.FETCHING_METADATA, start_stage) and job.title:
                    media_info = MediaInfo(
                        title=job.title,
                        year=job.year,
                        disc_type=job.disc_type,
                        tmdb_id=job.tmdb_id,
                    )

    if not _should_run(JobStatus.RIPPING, start_stage):
        raw_paths = sorted(raw_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True) if (raw_dir.exists() and (raw_dir / ".done").exists()) else []

    if not _should_run(JobStatus.TRANSCODING, start_stage):
        transcoded_paths = sorted(transcoded_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True) if (transcoded_dir.exists() and (transcoded_dir / ".done").exists()) else []

    try:
        # ── IDENTIFYING ────────────────────────────────────────────────────────
        if _should_run(JobStatus.IDENTIFYING, start_stage):
            await _update_job(job_id, status=JobStatus.IDENTIFYING, progress=0)
            log.info("Job %d: identifying disc %s", job_id, drive_path)

            titles = await ripper.get_disc_info()
            if not titles:
                raise RuntimeError(
                    "No valid titles found on disc (all shorter than the minimum duration)"
                )

            disc_type_hint = DiscType.TV_SHOW if ripper.is_tv_show_hint(titles) else DiscType.MOVIE
            await _update_job(
                job_id,
                disc_type=disc_type_hint,
                titles_json=json.dumps([dataclasses.asdict(t) for t in titles]),
            )

            titles_to_rip = titles if disc_type_hint == DiscType.TV_SHOW else [ripper.select_main_title(titles)]

        # ── FETCHING METADATA ──────────────────────────────────────────────────
        if _should_run(JobStatus.FETCHING_METADATA, start_stage):
            await _update_job(job_id, status=JobStatus.FETCHING_METADATA, progress=0)
            log.info("Job %d: fetching metadata for %r", job_id, disc_label)

            media_info = await metadata_svc.identify(
                disc_label or "", disc_type_hint
            )
            if isinstance(media_info, list):
                candidates_json = json.dumps([
                    {
                        "tmdb_id": m.tmdb_id,
                        "title": m.title,
                        "year": m.year,
                        "disc_type": m.disc_type.value,
                        "overview": m.overview,
                    }
                    for m in media_info
                ])
                await _update_job(job_id, candidates=candidates_json)
                log.info("Job %d: multiple matches found (%d), ripping now — awaiting user selection before transcode", job_id, len(media_info))
            elif isinstance(media_info, MediaInfo):
                await _update_job(
                    job_id,
                    title=media_info.title,
                    year=media_info.year,
                    disc_type=media_info.disc_type,
                    tmdb_id=media_info.tmdb_id,
                )

        # ── RESUME CHECK ───────────────────────────────────────────────────────
        if start_stage == JobStatus.IDENTIFYING:
            resume_raw, resume_transcoded, prior_job_id = await _find_resumable_paths(disc_label, job_id)

        # ── RIPPING (skipped if resuming from raw or transcoded output) ────────
        if _should_run(JobStatus.RIPPING, start_stage):
            if resume_transcoded:
                raw_paths = resume_raw  # may be empty; only needed for cleanup reference
            elif resume_raw:
                raw_paths = resume_raw
                log.info("Job %d: skipping rip — reusing raw output from job %d", job_id, prior_job_id)
            elif titles_to_rip:
                await _update_job(job_id, status=JobStatus.RIPPING, progress=0)
                log.info("Job %d: ripping %d title(s)", job_id, len(titles_to_rip))
                raw_paths = []
                for i, title in enumerate(titles_to_rip):
                    path = await ripper.rip(
                        title.id,
                        raw_dir,
                        on_progress=_stage_progress(job_id, i, len(titles_to_rip)),
                        expected_bytes=title.expected_bytes,
                    )
                    raw_paths.append(path)
                (raw_dir / ".done").touch()

        # Pause before transcoding if user hasn't selected a match yet.
        if _should_run(JobStatus.RIPPING, start_stage):
            async with AsyncSessionLocal() as db:
                job = await db.get(Job, job_id)
            if job is not None and job.candidates is not None:
                await _update_job(job_id, status=JobStatus.AWAITING_SELECTION)
                log.info("Job %d: ripping complete, awaiting metadata selection before transcode", job_id)
                return

        # ── TRANSCODING (skipped if resuming from transcoded output) ───────────
        if _should_run(JobStatus.TRANSCODING, start_stage):
            if resume_transcoded:
                transcoded_paths = resume_transcoded
                log.info("Job %d: skipping transcode — reusing transcoded output from job %d", job_id, prior_job_id)
            elif raw_paths:
                await _update_job(job_id, status=JobStatus.TRANSCODING, progress=0)
                log.info("Job %d: transcoding %d file(s)", job_id, len(raw_paths))
                transcoded_paths = []
                for i, raw_path in enumerate(raw_paths):
                    out = transcoded_dir / raw_path.name
                    await transcoder.transcode(
                        raw_path,
                        out,
                        on_progress=_stage_progress(job_id, i, len(raw_paths)),
                    )
                    transcoded_paths.append(out)
                (transcoded_dir / ".done").touch()

        # ── ORGANIZING ────────────────────────────────────────────────────────
        await _update_job(job_id, status=JobStatus.ORGANIZING, progress=0)
        if not transcoded_paths:
            log.warning("Job %d: organizing with no transcoded files — marking complete", job_id)
        else:
            log.info("Job %d: organizing %d file(s)", job_id, len(transcoded_paths))

        for i, path in enumerate(transcoded_paths):
            dest = organizer.build_destination(media_info, disc_label, episode_num=i + 1)
            await organizer.move(path, dest)

        shutil.rmtree(job_temp, ignore_errors=True)
        if prior_job_id is not None:
            shutil.rmtree(settings.temp_path / str(prior_job_id), ignore_errors=True)

        await _update_job(job_id, status=JobStatus.COMPLETE, progress=100)
        log.info("Job %d complete", job_id)

    except Exception as exc:
        log.exception("Job %d failed: %s", job_id, exc)
        await _update_job(job_id, status=JobStatus.FAILED, error_message=str(exc))


async def _process_reruns(queue: asyncio.Queue[tuple[int, JobStatus]]) -> None:
    while True:
        try:
            job_id, start_stage = await queue.get()
        except asyncio.CancelledError:
            return

        try:
            async with AsyncSessionLocal() as db:
                job = await db.get(Job, job_id)

            if job is None:
                log.warning("Rerun requested for unknown job %d — skipping", job_id)
                continue

            asyncio.create_task(
                _run_pipeline(job_id, job.drive_path, job.disc_label, start_stage),
                name=f"rerun-{job_id}",
            )
        except Exception:
            log.exception("Failed to start rerun for job %d", job_id)
        finally:
            queue.task_done()


async def _process_jobs(queue: asyncio.Queue[tuple[str, str | None]]) -> None:
    while True:
        try:
            drive_path, disc_label = await queue.get()
        except asyncio.CancelledError:
            return

        try:
            async with AsyncSessionLocal() as db:
                if disc_label:
                    prior_id = await db.scalar(
                        select(Job.id)
                        .where(Job.disc_label == disc_label, Job.status == JobStatus.COMPLETE)
                        .limit(1)
                    )
                    if prior_id is not None:
                        log.info(
                            "Disc %r already processed successfully (job %d) — skipping",
                            disc_label,
                            prior_id,
                        )
                        continue

                job = Job(
                    drive_path=drive_path,
                    disc_label=disc_label,
                    status=JobStatus.DETECTED,
                )
                db.add(job)
                await db.commit()
                await db.refresh(job)
                job_id = job.id
                log.info("Job %d created for %s", job_id, drive_path)

            # Each disc runs its pipeline independently so multiple drives work concurrently.
            asyncio.create_task(
                _run_pipeline(job_id, drive_path, disc_label),
                name=f"pipeline-{job_id}",
            )
        except Exception:
            log.exception("Failed to create job for drive %s", drive_path)
        finally:
            queue.task_done()


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )

    settings.temp_path.mkdir(parents=True, exist_ok=True)

    await init_db()
    log.info("Database initialized at %s", settings.db_path)

    terminal = {JobStatus.COMPLETE, JobStatus.FAILED}
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.status.not_in(terminal)))
        interrupted = result.scalars().all()
        for job in interrupted:
            job.status = JobStatus.FAILED
            job.error_message = "Interrupted by daemon restart"
        if interrupted:
            await db.commit()
            log.info("Marked %d interrupted job(s) as failed", len(interrupted))

    job_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
    rerun_queue: asyncio.Queue[tuple[int, JobStatus]] = asyncio.Queue()

    async def _on_disc_inserted(drive_path: str, disc_label: str | None) -> None:
        await job_queue.put((drive_path, disc_label))

    detector = DiscDetector(on_disc_inserted=_on_disc_inserted)

    server_config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(server_config)

    app.state.rerun_queue = rerun_queue

    log.info("Web UI available at http://%s:%d", settings.host, settings.port)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(server.serve(), name="web-server")
        tg.create_task(detector.run(), name="disc-detector")
        tg.create_task(_process_jobs(job_queue), name="job-processor")
        tg.create_task(_process_reruns(rerun_queue), name="rerun-processor")
