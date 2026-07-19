"""Cache-Control headers on the built SPA's StaticFiles mount.

Nix sets every file's mtime to the Unix epoch for build reproducibility.
Without an explicit Cache-Control, browsers computing heuristic freshness
from that ancient Last-Modified treat a response as fresh for years, so the
first version ever fetched gets silently reused forever, even after the
server is rebuilt with new content. These tests pin down the headers that
prevent that: long-lived immutable caching for Vite's content-hashed
assets/, no-cache for everything else so a rebuild is picked up immediately.
"""
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from jacques.api.app import _STATIC_DIR, app


@pytest_asyncio.fixture
async def static_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _any_asset_filename() -> str:
    assets_dir = Path(_STATIC_DIR) / "assets"
    return next(p.name for p in assets_dir.iterdir() if p.is_file())


@pytest.mark.asyncio
async def test_index_html_is_no_cache(static_client):
    response = await static_client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_hashed_asset_is_immutable(static_client):
    response = await static_client.get(f"/assets/{_any_asset_filename()}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_favicon_is_no_cache(static_client):
    response = await static_client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
