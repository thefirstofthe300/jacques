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


@pytest.mark.asyncio
async def test_identify_returns_list_for_close_matches():
    """Two results with popularity 100 and 50 (ratio 2×, below 3× threshold) → list."""
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 1, "title": "Close A", "release_date": "2020-01-01", "popularity": 100.0},
                    {"id": 2, "title": "Close B", "release_date": "2019-06-01", "popularity": 50.0},
                ]
            })
        )
        result = await svc.identify("Close Match", DiscType.MOVIE)

    assert isinstance(result, list)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_identify_returns_single_for_clear_winner():
    """Two results with popularity 300 and 50 (ratio 6×, above 3× threshold) → single MediaInfo."""
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 10, "title": "Clear Winner", "release_date": "2022-03-01", "popularity": 300.0},
                    {"id": 11, "title": "Distant Second", "release_date": "2021-08-01", "popularity": 50.0},
                ]
            })
        )
        result = await svc.identify("Clear Winner", DiscType.MOVIE)

    assert isinstance(result, MediaInfo)
    assert result.tmdb_id == 10
    assert result.title == "Clear Winner"


@pytest.mark.asyncio
async def test_identify_returns_single_for_zero_popularity_second():
    """Second result has popularity 0 → guard triggers, returns single top MediaInfo."""
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 20, "title": "Top Result", "release_date": "2023-05-01", "popularity": 75.0},
                    {"id": 21, "title": "Zero Pop", "release_date": "2022-11-01", "popularity": 0.0},
                ]
            })
        )
        result = await svc.identify("Top Result", DiscType.MOVIE)

    assert isinstance(result, MediaInfo)
    assert result.tmdb_id == 20


@pytest.mark.asyncio
async def test_identify_returns_single_for_exactly_one_result():
    """Exactly one result → returns MediaInfo, not a list."""
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 30, "title": "Only Film", "release_date": "2024-01-01", "popularity": 42.0},
                ]
            })
        )
        result = await svc.identify("Only Film", DiscType.MOVIE)

    assert isinstance(result, MediaInfo)
    assert result.tmdb_id == 30


# ---------------------------------------------------------------------------
# lookup_by_id tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_by_id_movie():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/movie/550").mock(
            return_value=httpx.Response(200, json={
                "title": "Fight Club",
                "release_date": "1999-10-15",
                "overview": "An insomniac office worker forms an underground fight club.",
                "popularity": 50.0,
            })
        )
        result = await svc.lookup_by_id(550, DiscType.MOVIE)

    assert result.title == "Fight Club"
    assert result.year == 1999
    assert result.disc_type == DiscType.MOVIE
    assert result.tmdb_id == 550
    assert result.popularity == 50.0
    assert "insomniac" in result.overview


@pytest.mark.asyncio
async def test_lookup_by_id_tv():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/tv/1396").mock(
            return_value=httpx.Response(200, json={
                "name": "Breaking Bad",
                "first_air_date": "2008-01-20",
                "overview": "A chemistry teacher turns to cooking meth.",
                "popularity": 100.0,
            })
        )
        result = await svc.lookup_by_id(1396, DiscType.TV_SHOW)

    assert result.title == "Breaking Bad"
    assert result.year == 2008
    assert result.disc_type == DiscType.TV_SHOW
    assert result.tmdb_id == 1396
    assert result.popularity == 100.0


@pytest.mark.asyncio
async def test_lookup_by_id_not_found():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/movie/999999").mock(
            return_value=httpx.Response(404, json={"status_message": "The resource you requested could not be found."})
        )
        with pytest.raises(ValueError, match="999999"):
            await svc.lookup_by_id(999999, DiscType.MOVIE)


@pytest.mark.asyncio
async def test_lookup_by_id_unknown_disc_type_treated_as_movie():
    svc = MetadataService(api_key="testkey")

    with respx.mock:
        respx.get(f"{_BASE}/movie/550").mock(
            return_value=httpx.Response(200, json={
                "title": "Fight Club",
                "release_date": "1999-10-15",
                "overview": "An insomniac office worker forms an underground fight club.",
                "popularity": 50.0,
            })
        )
        result = await svc.lookup_by_id(550, DiscType.UNKNOWN)

    assert result.title == "Fight Club"
    assert result.disc_type == DiscType.MOVIE
    assert result.tmdb_id == 550
