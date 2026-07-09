import httpx
import pytest
import respx

from jacques.models.job import DiscType
from jacques.services.metadata import MediaInfo, MetadataService

_BASE = "https://api.themoviedb.org/3"


@pytest.mark.asyncio
async def test_identify_movie():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 123, "title": "Inception", "release_date": "2010-07-16", "popularity": 100.0},
                ]
            })
        )
        result = await svc.identify("Inception", DiscType.MOVIE)

    assert result is not None
    assert result.title == "Inception"
    assert result.year == 2010
    assert result.disc_type == DiscType.MOVIE
    assert result.tmdb_id == 123


@pytest.mark.asyncio
async def test_identify_tv_show():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/tv").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 456, "name": "Breaking Bad", "first_air_date": "2008-01-20", "popularity": 200.0},
                ]
            })
        )
        result = await svc.identify("Breaking Bad", DiscType.TV_SHOW)

    assert result is not None
    assert result.title == "Breaking Bad"
    assert result.year == 2008
    assert result.disc_type == DiscType.TV_SHOW
    assert result.tmdb_id == 456


@pytest.mark.asyncio
async def test_identify_returns_none_without_api_key():
    result = await MetadataService(api_key="").identify("Something", DiscType.MOVIE)
    assert result is None


@pytest.mark.asyncio
async def test_identify_selects_highest_popularity():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 1, "title": "Low Pop", "release_date": "2020-01-01", "popularity": 5.0},
                    {"id": 2, "title": "High Pop", "release_date": "2021-06-15", "popularity": 999.0},
                ]
            })
        )
        result = await svc.identify("Movie", DiscType.MOVIE)

    assert result is not None
    assert result.tmdb_id == 2
    assert result.title == "High Pop"


@pytest.mark.asyncio
async def test_identify_falls_back_to_tv_when_movie_empty():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.get(f"{_BASE}/search/tv").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 789, "name": "Some Show", "first_air_date": "2020-03-01", "popularity": 50.0},
                ]
            })
        )
        result = await svc.identify("Some Show", DiscType.MOVIE)

    assert result is not None
    assert result.disc_type == DiscType.TV_SHOW


@pytest.mark.asyncio
async def test_identify_falls_back_to_movie_when_tv_empty():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/tv").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 321, "title": "A Film", "release_date": "2015-05-01", "popularity": 80.0},
                ]
            })
        )
        result = await svc.identify("A Film", DiscType.TV_SHOW)

    assert result is not None
    assert result.disc_type == DiscType.MOVIE


@pytest.mark.asyncio
async def test_identify_returns_none_when_all_searches_empty():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.get(f"{_BASE}/search/tv").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        result = await svc.identify("UNKNOWN_DISC_XYZ", DiscType.MOVIE)

    assert result is None


@pytest.mark.asyncio
async def test_identify_returns_none_on_http_error():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(503, json={"status_message": "Service unavailable"})
        )
        respx.get(f"{_BASE}/search/tv").mock(
            return_value=httpx.Response(503, json={"status_message": "Service unavailable"})
        )
        result = await svc.identify("Something", DiscType.MOVIE)

    assert result is None


@pytest.mark.asyncio
async def test_identify_handles_missing_release_date():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 555, "title": "No Date Film", "release_date": "", "popularity": 10.0},
                ]
            })
        )
        result = await svc.identify("No Date Film", DiscType.MOVIE)

    assert result is not None
    assert result.year is None
