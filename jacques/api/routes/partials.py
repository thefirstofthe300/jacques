from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...database import get_db
from ...models.job import DiscType, Job
from ...services.metadata import MetadataService

router = APIRouter(prefix="/partials", tags=["partials"])


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/job_list.html", {"jobs": jobs})


@router.get("/jobs/{job_id}/candidates", response_class=HTMLResponse)
async def job_candidates_partial(
    request: Request,
    job_id: int,
    disc_type: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
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
    candidates = [
        {
            "title": r.title,
            "year": r.year,
            "disc_type": r.disc_type.value,
            "tmdb_id": r.tmdb_id,
            "overview": r.overview,
        }
        for r in results
    ]
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request,
        "partials/candidate_list.html",
        {"candidates": candidates, "job_id": job_id},
    )
