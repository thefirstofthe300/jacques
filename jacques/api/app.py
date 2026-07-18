from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .routes import jobs as jobs_router

_STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(title="Jacques", docs_url="/api/docs", lifespan=_lifespan)

app.include_router(jobs_router.router)


class _CachedStaticFiles(StaticFiles):
    """Serves the built SPA with cache headers Vite's output actually needs.

    Nix sets every file's mtime to the Unix epoch for build reproducibility.
    Browsers computing heuristic freshness from a Last-Modified that old
    treat the response as fresh for years, so without an explicit
    Cache-Control the first version a browser ever fetches gets reused
    forever, even after the server is rebuilt with new content. Vite's
    content-hashed filenames under assets/ make long-lived immutable caching
    safe; everything else (index.html, favicon.svg) must revalidate on every
    request so a rebuild is picked up immediately.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


# Must be registered last: Starlette matches mounts/routes in registration
# order, and a Mount("/") registered first would shadow every other route,
# including /api/*.
app.mount("/", _CachedStaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
