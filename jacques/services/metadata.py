import logging
import re
from dataclasses import dataclass

import httpx
import wordsegment

from ..models.job import DiscType

log = logging.getLogger(__name__)

_TMDB_BASE = "https://api.themoviedb.org/3"

# Strips trailing disc edition codes like _UPB75, _BD50, _UHD, _4K.
_EDITION_SUFFIX_RE = re.compile(r"(?:_[A-Z0-9]{2,8})+$")

wordsegment.load()


def _normalize_label(label: str) -> str:
    """Convert a raw disc label to a TMDb-friendly search query.

    DARKESTHOUR_UPB75 → Darkest Hour
    THE_DARK_KNIGHT_RISES_BD50 → The Dark Knight Rises
    DUNKIRK → Dunkirk
    """
    label = _EDITION_SUFFIX_RE.sub("", label)
    # Split on underscores first; segment any remaining concatenated tokens.
    tokens = []
    for part in label.split("_"):
        if part:
            tokens.extend(wordsegment.segment(part.lower()))
    return " ".join(tokens).title()


@dataclass
class MediaInfo:
    title: str
    year: int | None
    disc_type: DiscType
    tmdb_id: int | None
    overview: str = ""
    popularity: float = 0.0


class MetadataService:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def identify(
        self, disc_label: str, disc_type_hint: DiscType
    ) -> "MediaInfo | list[MediaInfo] | None":
        """Look up disc_label on TMDb.

        Returns:
          - None if no API key or no results found.
          - A single MediaInfo if there is exactly one result, or the top result
            is a clear popularity winner (≥ 3× the second result's popularity).
          - A list[MediaInfo] if multiple close matches exist (for user selection).

        Tries the hinted disc type first, then falls back to the other type.
        """
        if not self._api_key:
            log.warning("TMDb API key not configured; skipping metadata lookup")
            return None

        query = _normalize_label(disc_label)
        log.info("TMDb query: %r (from label %r)", query, disc_label)

        async with httpx.AsyncClient() as client:
            if disc_type_hint == DiscType.TV_SHOW:
                primary = await self._search_tv(client, query)
                fallback = await self._search_movie(client, query) if not primary else []
            else:
                primary = await self._search_movie(client, query)
                fallback = await self._search_tv(client, query) if not primary else []

        candidates = primary + fallback

        if not candidates:
            log.warning("No TMDb match found for %r", disc_label)
            return None

        if len(candidates) == 1:
            return candidates[0]

        top, second = candidates[0], candidates[1]
        if second.popularity <= 0 or top.popularity >= second.popularity * 3:
            return top

        return candidates

    async def _search_movie(
        self, client: httpx.AsyncClient, query: str
    ) -> list[MediaInfo]:
        try:
            resp = await client.get(
                f"{_TMDB_BASE}/search/movie",
                params={"api_key": self._api_key, "query": query},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("TMDb movie search error: %s", exc)
            return []

        results = resp.json().get("results", [])
        if not results:
            return []

        sorted_results = sorted(results, key=lambda r: r.get("popularity", 0), reverse=True)[:5]
        items = []
        for r in sorted_results:
            release = r.get("release_date") or ""
            overview = r.get("overview") or ""
            items.append(MediaInfo(
                title=r["title"],
                year=int(release[:4]) if len(release) >= 4 else None,
                disc_type=DiscType.MOVIE,
                tmdb_id=r.get("id"),
                overview=overview[:200],
                popularity=float(r.get("popularity", 0)),
            ))
        return items

    async def _search_tv(
        self, client: httpx.AsyncClient, query: str
    ) -> list[MediaInfo]:
        try:
            resp = await client.get(
                f"{_TMDB_BASE}/search/tv",
                params={"api_key": self._api_key, "query": query},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("TMDb TV search error: %s", exc)
            return []

        results = resp.json().get("results", [])
        if not results:
            return []

        sorted_results = sorted(results, key=lambda r: r.get("popularity", 0), reverse=True)[:5]
        items = []
        for r in sorted_results:
            first_air = r.get("first_air_date") or ""
            overview = r.get("overview") or ""
            items.append(MediaInfo(
                title=r["name"],
                year=int(first_air[:4]) if len(first_air) >= 4 else None,
                disc_type=DiscType.TV_SHOW,
                tmdb_id=r.get("id"),
                overview=overview[:200],
                popularity=float(r.get("popularity", 0)),
            ))
        return items
