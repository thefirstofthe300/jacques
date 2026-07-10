import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...database import get_db
from ...models.job import DiscType, Job, JobStatus
from ...services.metadata import MetadataService

_RERUN_ENTRY_STAGES: dict[str, JobStatus] = {
    "identifying": JobStatus.IDENTIFYING,
    "fetching_metadata": JobStatus.FETCHING_METADATA,
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

    model_config = {"from_attributes": True}

    @classmethod
    def from_job(cls, job: Job) -> "JobResponse":
        return cls(
            id=job.id,
            drive_path=job.drive_path,
            disc_label=job.disc_label,
            disc_uuid=job.disc_uuid,
            disc_type=job.disc_type.value,
            status=job.status.value,
            title=job.title,
            year=job.year,
            progress=job.progress,
            error_message=job.error_message,
            display_name=job.display_name,
            is_active=job.is_active,
            created_at=job.created_at.isoformat(),
            updated_at=job.updated_at.isoformat(),
        )


@router.get("", response_model=list[JobResponse])
async def list_jobs(db: AsyncSession = Depends(get_db)) -> list[JobResponse]:
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()
    return [JobResponse.from_job(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.from_job(job)


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

    job.status = target_status
    job.error_message = None
    job.progress = 0
    await db.commit()

    queue = getattr(request.app.state, "rerun_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")
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

    selectable = {JobStatus.AWAITING_SELECTION}
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

    was_paused = job.status == JobStatus.AWAITING_SELECTION

    job.title = title
    job.year = year
    job.tmdb_id = tmdb_id
    job.disc_type = disc_type
    job.candidates = None
    job.error_message = None
    if was_paused:
        job.status = JobStatus.TRANSCODING
        job.progress = 0
    await db.commit()

    if was_paused:
        queue = getattr(request.app.state, "rerun_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Service not ready")
        await queue.put((job_id, JobStatus.TRANSCODING))

    return JSONResponse(status_code=202, content={"job_id": job_id, "tmdb_id": tmdb_id})


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.is_active:
        raise HTTPException(status_code=409, detail="Cannot delete an active job")

    await db.delete(job)
    await db.commit()


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

    job.status = JobStatus.IDENTIFYING
    job.progress = 0
    job.error_message = None
    await db.commit()

    queue = getattr(request.app.state, "rerun_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    await queue.put((job_id, JobStatus.IDENTIFYING))

    return JSONResponse(status_code=202, content={"job_id": job_id})
