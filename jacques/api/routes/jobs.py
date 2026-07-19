import asyncio
import json
import logging
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...database import get_db
from ...models.job import DiscType, Job, JobStatus
from ...services.broadcaster import Broadcaster
from ...services.metadata import MetadataService

_RERUN_ENTRY_STAGES: dict[str, JobStatus] = {
    "identifying": JobStatus.IDENTIFYING,
    "fetching_metadata": JobStatus.FETCHING_METADATA,
    "ripping": JobStatus.RIPPING,
    "transcoding": JobStatus.TRANSCODING,
    "organizing": JobStatus.ORGANIZING,
}

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    id: int
    drive_path: str
    disc_label: str | None
    disc_uuid: str | None
    disc_type: str
    status: str
    title: str | None
    year: int | None
    progress: int
    error_message: str | None
    display_name: str
    is_active: bool
    created_at: str
    updated_at: str
    candidates: list[dict]
    titles: list[dict]
    episode_assignments: dict
    selected_title_id: int | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_job(cls, job: Job) -> "JobResponse":
        return cls(**job.to_response_dict())


class CandidateResponse(BaseModel):
    title: str
    year: int | None
    disc_type: str
    tmdb_id: int
    overview: str | None


def publish_job_event(broadcaster, job: Job, event_type: str = "job_upserted") -> None:
    """Publish a job-upserted event carrying the full job payload, if a
    broadcaster is wired up. No-ops if `broadcaster` is None (not yet set on
    `app.state`, e.g. in tests that don't need it).
    """
    if broadcaster is None:
        return
    broadcaster.publish({"type": event_type, "job": JobResponse.from_job(job).model_dump(mode="json")})


def publish_job_deleted(broadcaster, job_id: int) -> None:
    """Publish a job-deleted event carrying just the job's id.

    Callers must capture `job_id` before deleting the row — once the ORM
    object is deleted and the session committed, its attributes are no
    longer safe to access.
    """
    if broadcaster is None:
        return
    broadcaster.publish({"type": "job_deleted", "job_id": job_id})


@router.get("", response_model=list[JobResponse])
async def list_jobs(db: AsyncSession = Depends(get_db)) -> list[JobResponse]:
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()
    return [JobResponse.from_job(j) for j in jobs]


@router.get("/stream")
async def stream_jobs(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of job mutations.

    Emits `event: job-update` frames whose `data:` is the JSON-encoded event
    dict published by `publish_job_event`/`publish_job_deleted` (shaped
    `{"type": "job_upserted", "job": {...}}` or `{"type": "job_deleted", "job_id": ...}`).
    Replaces HTMX polling of the job list/detail partials.

    Returns 503 if the broadcaster isn't wired up yet, or if
    `Broadcaster.max_subscribers` connections are already active (this is a
    local-network, no-auth app, so the subscriber count is capped to bound
    memory/file-descriptor usage rather than allowing unlimited connections).
    """
    broadcaster = getattr(request.app.state, "job_events", None)
    if broadcaster is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        queue = broadcaster.subscribe()
    except Broadcaster.SubscriberLimitReached:
        raise HTTPException(status_code=503, detail="Too many active connections; try again later")

    async def event_stream():
        try:
            # Send an immediate byte so the connection is unambiguously
            # "open" the moment a client subscribes, rather than leaving the
            # response with zero bytes sent until the first real job event
            # (which may be minutes away, or never, if nothing is currently
            # active) — some browsers don't reliably fire EventSource's
            # `open` event on a response that hasn't sent anything yet.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue
                yield f"event: job-update\ndata: {json.dumps(event)}\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.from_job(job)


@router.get("/{job_id}/candidates", response_model=list[CandidateResponse])
async def job_candidates(
    job_id: int,
    disc_type: str,
    db: AsyncSession = Depends(get_db),
) -> list[CandidateResponse]:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        search_type = DiscType(disc_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid disc_type: {disc_type!r}")

    results = await MetadataService(settings.tmdb_api_key).search(
        job.disc_label or "", search_type
    )
    return [
        CandidateResponse(
            title=r.title,
            year=r.year,
            disc_type=r.disc_type.value,
            tmdb_id=r.tmdb_id,
            overview=r.overview,
        )
        for r in results
    ]


@router.post("/{job_id}/rerun/{stage}", status_code=202)
async def rerun_job(
    job_id: int,
    stage: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    target_status = _RERUN_ENTRY_STAGES.get(stage)
    if target_status is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid rerun stage {stage!r}. Valid stages: {', '.join(_RERUN_ENTRY_STAGES)}",
        )

    if job.is_active:
        raise HTTPException(
            status_code=409,
            detail="Job is currently active; wait for it to finish before rerunning",
        )

    if stage == "ripping" and not job.titles_json:
        raise HTTPException(
            status_code=409,
            detail="No disc titles found for this job; rerun from identifying instead",
        )

    if stage == "transcoding":
        done_marker = settings.temp_path / str(job_id) / "raw" / ".done"
        if not done_marker.exists():
            raise HTTPException(
                status_code=409,
                detail="No completed raw files found for this job",
            )

    if stage == "organizing":
        done_marker = settings.temp_path / str(job_id) / "transcoded" / ".done"
        if not done_marker.exists():
            raise HTTPException(
                status_code=409,
                detail="No completed transcoded files found for this job",
            )

    queue = getattr(request.app.state, "rerun_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    job.status = target_status
    job.error_message = None
    job.progress = 0
    await db.commit()
    publish_job_event(getattr(request.app.state, "job_events", None), job)

    await queue.put((job_id, target_status))

    return JSONResponse(status_code=202, content={"job_id": job_id, "stage": stage})


@router.post("/{job_id}/select/{tmdb_id}", status_code=202)
async def select_match(
    job_id: int,
    tmdb_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    disc_type: str | None = None,
) -> JSONResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    selectable = {JobStatus.AWAITING_SELECTION, JobStatus.RIPPING_AWAITING_SELECTION}
    if job.status not in selectable:
        raise HTTPException(status_code=409, detail="Job is not awaiting selection")

    candidate: dict | None = None
    if job.candidates:
        stored = json.loads(job.candidates)
        candidate = next((c for c in stored if c["tmdb_id"] == tmdb_id), None)

    if candidate is not None:
        title = candidate["title"]
        year = candidate["year"]
        disc_type = DiscType(candidate["disc_type"])
    else:
        try:
            lookup_type = DiscType(disc_type) if disc_type else job.disc_type
            media_info = await MetadataService(settings.tmdb_api_key).lookup_by_id(tmdb_id, lookup_type)
        except ValueError as e:
            log.warning("TMDB lookup failed for job %d, tmdb_id %d: %s", job_id, tmdb_id, e)
            raise HTTPException(status_code=404, detail="TMDB ID not found")
        title = media_info.title
        year = media_info.year
        disc_type = media_info.disc_type

    fully_paused = job.status == JobStatus.AWAITING_SELECTION
    still_ripping = job.status == JobStatus.RIPPING_AWAITING_SELECTION

    queue = None
    if fully_paused:
        queue = getattr(request.app.state, "rerun_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Service not ready")

    job.title = title
    job.year = year
    job.tmdb_id = tmdb_id
    job.disc_type = disc_type
    job.candidates = None
    job.error_message = None
    if fully_paused:
        job.status = JobStatus.TRANSCODING
        job.progress = 0
    elif still_ripping:
        job.status = JobStatus.RIPPING
    await db.commit()
    publish_job_event(getattr(request.app.state, "job_events", None), job)

    if fully_paused:
        await queue.put((job_id, JobStatus.TRANSCODING))

    return JSONResponse(status_code=202, content={"job_id": job_id, "tmdb_id": tmdb_id})


class EpisodeAssignment(BaseModel):
    title_id: int
    season: int
    episode: int
    name: str


@router.post("/{job_id}/assign-episodes", status_code=202)
async def assign_episodes(
    job_id: int,
    body: list[EpisodeAssignment],
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.AWAITING_EPISODE_ASSIGNMENT:
        raise HTTPException(status_code=409, detail="Job is not awaiting episode assignment")

    parsed_title_ids = {t["id"] for t in job.parsed_titles}
    submitted_title_ids = {a.title_id for a in body}

    if len(body) != len(submitted_title_ids):
        raise HTTPException(status_code=400, detail="duplicate title_ids in request body")

    if submitted_title_ids != parsed_title_ids:
        missing = parsed_title_ids - submitted_title_ids
        extra = submitted_title_ids - parsed_title_ids
        detail_parts = []
        if missing:
            detail_parts.append(f"missing title_ids: {sorted(missing)}")
        if extra:
            detail_parts.append(f"unknown title_ids: {sorted(extra)}")
        raise HTTPException(status_code=400, detail="; ".join(detail_parts))

    queue = getattr(request.app.state, "rerun_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    job.episode_assignments = json.dumps({
        str(a.title_id): {"season": a.season, "episode": a.episode, "name": a.name}
        for a in body
    })
    job.status = JobStatus.TRANSCODING
    job.progress = 0
    job.error_message = None
    await db.commit()
    publish_job_event(getattr(request.app.state, "job_events", None), job)

    await queue.put((job_id, JobStatus.TRANSCODING))

    return JSONResponse(status_code=202, content={"job_id": job_id, "assigned": len(body)})


@router.post("/{job_id}/keep-title/{title_id}", status_code=202)
async def keep_title(
    job_id: int,
    title_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.AWAITING_TITLE_SELECTION:
        raise HTTPException(status_code=409, detail="Job is not awaiting title selection")

    parsed_title_ids = {t["id"] for t in job.parsed_titles}
    if title_id not in parsed_title_ids:
        raise HTTPException(status_code=400, detail=f"Unknown title_id {title_id}")

    queue = getattr(request.app.state, "rerun_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    job.selected_title_id = title_id
    job.status = JobStatus.TRANSCODING
    job.progress = 0
    job.error_message = None

    for other_id in parsed_title_ids - {title_id}:
        other_dir = settings.temp_path / str(job_id) / "raw" / str(other_id)
        try:
            shutil.rmtree(other_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Job %d: failed to clean up discarded title %s raw output: %s", job_id, other_id, exc)

    await db.commit()
    publish_job_event(getattr(request.app.state, "job_events", None), job)

    await queue.put((job_id, JobStatus.TRANSCODING))

    return JSONResponse(status_code=202, content={"job_id": job_id, "title_id": title_id})


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.is_active:
        raise HTTPException(status_code=409, detail="Cannot delete an active job")

    deleted_job_id = job.id
    await db.delete(job)
    await db.commit()
    publish_job_deleted(getattr(request.app.state, "job_events", None), deleted_job_id)


@router.post("/{job_id}/rerip", status_code=202)
async def rerip_job(
    job_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.DUPLICATE_DETECTED:
        raise HTTPException(status_code=409, detail="Job is not a duplicate")

    queue = getattr(request.app.state, "rerun_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    job.status = JobStatus.IDENTIFYING
    job.progress = 0
    job.error_message = None
    await db.commit()
    publish_job_event(getattr(request.app.state, "job_events", None), job)

    await queue.put((job_id, JobStatus.IDENTIFYING))

    return JSONResponse(status_code=202, content={"job_id": job_id})
