import httpx
import pytest
import respx

from jacques.models.job import DiscType
from jacques.services.discdb import DiscDBService, DiscMatch

_BASE = "https://thediscdb.com/graphql"


@pytest.mark.asyncio
async def test_identify_by_hash_success():
    svc = DiscDBService()

    payload = {
        "data": {
            "mediaItems": [
                {
                    "title": "Breaking Bad",
                    "year": 2008,
                    "type": "Series",
                    "externalIds": {"tmdb": 1396, "imdb": "tt0903747", "tvdb": 81189},
                    "releases": [
                        {
                            "discs": [
                                {
                                    "contentHash": "abc123",
                                    "index": 0,
                                    "name": "Disc 1",
                                    "format": "BLURAY",
                                    "slug": "breaking-bad-s1d1",
                                    "titles": [
                                        {
                                            "index": 0,
                                            "sourceFile": "00001.mpls",
                                            "duration": 2820,
                                            "displaySize": "0:47:00",
                                            "size": 123456789,
                                            "segmentMap": "1",
                                            "hasItem": True,
                                            "item": {
                                                "title": "Pilot",
                                                "type": "Episode",
                                                "season": 1,
                                                "episode": 1,
                                            },
                                        },
                                        {
                                            "index": 1,
                                            "sourceFile": "00002.mpls",
                                            "duration": 2760,
                                            "displaySize": "0:46:00",
                                            "size": 123456000,
                                            "segmentMap": "2",
                                            "hasItem": True,
                                            "item": {
                                                "title": "Cat's in the Bag...",
                                                "type": "Episode",
                                                "season": 1,
                                                "episode": 2,
                                            },
                                        },
                                        {
                                            "index": 2,
                                            "sourceFile": "00003.mpls",
                                            "duration": 120,
                                            "displaySize": "0:02:00",
                                            "size": 1000,
                                            "segmentMap": "3",
                                            "hasItem": False,
                                        },
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    }

    with respx.mock:
        respx.post(_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await svc.identify_by_hash("somehash")

    assert isinstance(result, DiscMatch)
    assert result.media_info.title == "Breaking Bad"
    assert result.media_info.year == 2008
    assert result.media_info.disc_type == DiscType.TV_SHOW
    assert result.media_info.tmdb_id == 1396

    assert len(result.titles) == 3

    ep1, ep2, extra = result.titles

    assert ep1.source_file == "00001.mpls"
    assert ep1.has_item is True
    assert ep1.title == "Pilot"
    assert ep1.type == "Episode"
    assert ep1.season == 1
    assert ep1.episode == 1

    assert ep2.source_file == "00002.mpls"
    assert ep2.has_item is True
    assert ep2.season == 1
    assert ep2.episode == 2

    # the hasItem: false entry (no `item`) must still be present, not dropped,
    # with season/episode defaulting to None
    assert extra.source_file == "00003.mpls"
    assert extra.has_item is False
    assert extra.title == ""
    assert extra.type == ""
    assert extra.season is None
    assert extra.episode is None


@pytest.mark.asyncio
async def test_identify_by_hash_no_match():
    svc = DiscDBService()

    with respx.mock:
        respx.post(_BASE).mock(
            return_value=httpx.Response(200, json={"data": {"mediaItems": []}})
        )
        result = await svc.identify_by_hash("unknownhash")

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500])
async def test_identify_by_hash_returns_none_on_http_error(status_code):
    svc = DiscDBService()

    with respx.mock:
        respx.post(_BASE).mock(return_value=httpx.Response(status_code, json={}))
        result = await svc.identify_by_hash("somehash")

    assert result is None


@pytest.mark.asyncio
async def test_identify_by_hash_returns_none_on_network_error():
    svc = DiscDBService()

    with respx.mock:
        respx.post(_BASE).mock(side_effect=httpx.ConnectError("connection failed"))
        result = await svc.identify_by_hash("somehash")

    assert result is None


@pytest.mark.asyncio
async def test_identify_by_hash_missing_releases_degrades_to_empty_titles():
    svc = DiscDBService()

    payload = {
        "data": {
            "mediaItems": [
                {
                    "title": "Some Movie",
                    "year": 2020,
                    "type": "Movie",
                    "externalIds": {"tmdb": 42},
                    # "releases" is entirely absent
                }
            ]
        }
    }

    with respx.mock:
        respx.post(_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await svc.identify_by_hash("somehash")

    assert isinstance(result, DiscMatch)
    assert result.media_info.title == "Some Movie"
    assert result.media_info.tmdb_id == 42
    assert result.titles == []


@pytest.mark.asyncio
async def test_identify_by_hash_missing_titles_in_disc_degrades_to_empty_titles():
    svc = DiscDBService()

    payload = {
        "data": {
            "mediaItems": [
                {
                    "title": "Some Movie",
                    "year": 2020,
                    "type": "Movie",
                    "externalIds": {"tmdb": 42},
                    "releases": [
                        {
                            "discs": [
                                {
                                    "contentHash": "abc123",
                                    "index": 0,
                                    "name": "Disc 1",
                                    "format": "BLURAY",
                                    "slug": "some-movie",
                                    # "titles" is entirely absent
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    }

    with respx.mock:
        respx.post(_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await svc.identify_by_hash("somehash")

    assert isinstance(result, DiscMatch)
    assert result.titles == []


@pytest.mark.asyncio
async def test_identify_by_hash_filters_titles_to_matching_disc_content_hash():
    """Two releases, each with a disc sharing the same sourceFile value but
    different episode data. Only the disc whose contentHash matches the
    requested hash should contribute titles — not a conflated mix."""
    svc = DiscDBService()

    payload = {
        "data": {
            "mediaItems": [
                {
                    "title": "Breaking Bad",
                    "year": 2008,
                    "type": "Series",
                    "externalIds": {"tmdb": 1396, "imdb": "tt0903747", "tvdb": 81189},
                    "releases": [
                        {
                            "discs": [
                                {
                                    "contentHash": "disc-1-hash",
                                    "index": 0,
                                    "name": "Disc 1",
                                    "format": "BLURAY",
                                    "slug": "breaking-bad-s1d1",
                                    "titles": [
                                        {
                                            "index": 0,
                                            "sourceFile": "00800.mpls",
                                            "duration": 2820,
                                            "displaySize": "0:47:00",
                                            "size": 123456789,
                                            "segmentMap": "1",
                                            "hasItem": True,
                                            "item": {
                                                "title": "Pilot",
                                                "type": "Episode",
                                                "season": 1,
                                                "episode": 1,
                                            },
                                        },
                                    ],
                                }
                            ]
                        },
                        {
                            "discs": [
                                {
                                    "contentHash": "disc-2-hash",
                                    "index": 0,
                                    "name": "Disc 2",
                                    "format": "BLURAY",
                                    "slug": "breaking-bad-s1d2",
                                    "titles": [
                                        {
                                            "index": 0,
                                            "sourceFile": "00800.mpls",
                                            "duration": 2760,
                                            "displaySize": "0:46:00",
                                            "size": 123456000,
                                            "segmentMap": "1",
                                            "hasItem": True,
                                            "item": {
                                                "title": "Cat's in the Bag...",
                                                "type": "Episode",
                                                "season": 1,
                                                "episode": 2,
                                            },
                                        },
                                    ],
                                }
                            ]
                        },
                    ],
                }
            ]
        }
    }

    with respx.mock:
        respx.post(_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await svc.identify_by_hash("disc-2-hash")

    assert isinstance(result, DiscMatch)
    # Only disc-2's title(s) should be present, despite disc-1 having a title
    # with the same sourceFile.
    assert len(result.titles) == 1

    title = result.titles[0]
    assert title.source_file == "00800.mpls"
    assert title.title == "Cat's in the Bag..."
    assert title.season == 1
    assert title.episode == 2


@pytest.mark.asyncio
async def test_identify_by_hash_returns_none_when_response_too_large():
    svc = DiscDBService()

    with respx.mock:
        respx.post(_BASE).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"mediaItems": []}},
                headers={"content-length": str(6 * 1024 * 1024)},
            )
        )
        result = await svc.identify_by_hash("somehash")

    assert result is None


@pytest.mark.asyncio
async def test_identify_by_hash_unparseable_shape_returns_none():
    """A shape so unexpected that parsing raises (not just missing keys) must
    still degrade to None via the defensive catch-all, never propagate."""
    svc = DiscDBService()

    payload = {
        "data": {
            "mediaItems": [
                {
                    "title": "Some Movie",
                    "year": 2020,
                    "type": "Movie",
                    "externalIds": {"tmdb": 42},
                    # `discs` is a string rather than a list of dicts, so
                    # iterating it yields characters with no `.get` method,
                    # raising AttributeError inside _parse_media_item.
                    "releases": [{"discs": "not-a-list"}],
                }
            ]
        }
    }

    with respx.mock:
        respx.post(_BASE).mock(return_value=httpx.Response(200, json=payload))
        result = await svc.identify_by_hash("somehash")

    assert result is None
