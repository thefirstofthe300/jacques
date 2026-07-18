from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.job import Job
from .routes import jobs as jobs_router
from .routes import partials as partials_router

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_STATUS_CLASSES: dict[str, str] = {
    "detected": "bg-secondary",
    "identifying": "bg-info text-dark",
    "ripping": "bg-primary",
    "transcoding": "bg-primary",
    "fetching_metadata": "bg-warning text-dark",
    "organizing": "bg-warning text-dark",
    "awaiting_selection": "bg-warning text-dark",
    "ripping_awaiting_selection": "bg-warning text-dark",
    "awaiting_episode_assignment": "bg-warning text-dark",
    "awaiting_title_selection": "bg-warning text-dark",
    "complete": "bg-success",
    "failed": "bg-danger",
}


def _status_class(status: str) -> str:
    return _STATUS_CLASSES.get(status, "bg-secondary")


templates.env.filters["status_class"] = _status_class


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.templates = templates
    yield


app = FastAPI(title="Jacques", docs_url="/api/docs", lifespan=_lifespan)

app.include_router(jobs_router.router)
app.include_router(partials_router.router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()
    return templates.TemplateResponse(request, "index.html", {"jobs": jobs})
