from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...database import get_db
from ...models.job import DiscType, Job, JobStatus

_RERUN_ENTRY_STAGES: dict[str, JobStatus] = {
    "identifying": JobStatus.IDENTIFYING,
    "fetching_metadata": JobStatus.FETCHING_METADATA,
    "transcoding": JobStatus.TRANSCODING,
    "organizing": JobStatus.ORGANIZING,
}

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    id: int
    drive_path: str
    disc_label: str | None
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
