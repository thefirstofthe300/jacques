from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...models.job import Job

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
