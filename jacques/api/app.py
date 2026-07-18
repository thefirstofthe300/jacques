from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import jobs as jobs_router

_STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(title="Jacques", docs_url="/api/docs", lifespan=_lifespan)

app.include_router(jobs_router.router)

# Must be registered last: Starlette matches mounts/routes in registration
# order, and a Mount("/") registered first would shadow every other route,
# including /api/*.
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
