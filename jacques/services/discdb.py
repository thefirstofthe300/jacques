import logging
from dataclasses import dataclass

import httpx

from ..models.job import DiscType
from .metadata import MediaInfo

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://thediscdb.com/graphql"

# TheDiscDB has no official schema docs; this query shape was built from live
# GraphQL introspection against thediscdb.com/graphql (its own client and
# third-party clients reverse-engineer it the same way -- there's no other
# source of truth). `mediaItems` is the only paginated field in this query
# (hence `nodes`); the nested `releases`/`discs`/`titles` are plain lists.
# `contentHash` matching is case-insensitive server-side, so the hash is
# passed through as-is (Python's `hexdigest()` is already lowercase).
_QUERY = """
query LookupByHash($hash: String!) {
  mediaItems(where: { releases: { some: { discs: { some: { contentHash: { eq: $hash } } } } } }) {
    nodes {
      title
      year
      type
      externalids {
        tmdb
        imdb
        tvdb
      }
      releases {
        discs {
          contentHash
          index
          name
          format
          slug
          titles {
            index
            sourceFile
            duration
            displaySize
            size
            segmentMap
            hasItem
            item {
              title
              type
              season
              episode
            }
          }
        }
      }
    }
  }
}
"""

# Maps TheDiscDB's `type` field (reverse-engineered, values observed as "Movie"
# and "Series") onto our own DiscType enum. Public so other modules (e.g.
# daemon.py) can use it without reaching into a private name.
TYPE_MAP = {
    "Movie": DiscType.MOVIE,
    "Series": DiscType.TV_SHOW,
}

# A disc-lookup response is small nested JSON (a handful of titles per disc);
# anything past this is either a misconfigured discdb_base_url or a
# compromised/misbehaving upstream, so refuse to buffer it.
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _parse_int(value: str | None) -> int | None:
    """TheDiscDB's schema types tmdb/season/episode as `String`, not `Int`."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass
class DiscDBTitle:
    source_file: str
    has_item: bool
    title: str
    type: str
    season: int | None
    episode: int | None


@dataclass
class DiscMatch:
    media_info: MediaInfo
    titles: list[DiscDBTitle]


class DiscDBService:
    def __init__(self, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url

    async def identify_by_hash(self, content_hash: str) -> DiscMatch | None:
        """Look up a disc by its content hash on TheDiscDB.

        Returns None on any non-match, HTTP error (including rate limiting),
        or unparseable response — this client never raises.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(
                    self._base_url,
                    json={"query": _QUERY, "variables": {"hash": content_hash}},
                )
            except httpx.HTTPError as exc:
                log.warning("TheDiscDB request failed: %s", exc)
                return None

        if resp.status_code != 200:
            log.warning(
                "TheDiscDB request failed: HTTP %d for hash %r",
                resp.status_code,
                content_hash,
            )
            return None

        content_length = resp.headers.get("content-length")
        if content_length is not None:
            try:
                too_large = int(content_length) > _MAX_RESPONSE_BYTES
            except ValueError:
                log.warning(
                    "TheDiscDB returned unparseable Content-Length %r for hash %r",
                    content_length,
                    content_hash,
                )
            else:
                if too_large:
                    log.warning(
                        "TheDiscDB response too large (%s bytes, limit %d) for hash %r",
                        content_length,
                        _MAX_RESPONSE_BYTES,
                        content_hash,
                    )
                    return None

        try:
            payload = resp.json()
        except ValueError as exc:
            log.warning("TheDiscDB returned invalid JSON: %s", exc)
            return None

        errors = payload.get("errors")
        if errors:
            log.warning("TheDiscDB returned GraphQL errors: %s", errors)
            return None

        media_items = ((payload.get("data") or {}).get("mediaItems") or {}).get("nodes") or []
        if not media_items:
            log.warning("No TheDiscDB match found for content hash %r", content_hash)
            return None

        try:
            return self._parse_media_item(media_items[0], content_hash)
        except Exception as exc:
            # Defensive catch-all: this schema is reverse-engineered, not an
            # official spec, so an unexpected shape should degrade to "no
            # match" rather than crash the pipeline.
            log.warning("Failed to parse TheDiscDB response: %s", exc)
            return None

    def _parse_media_item(self, item: dict, content_hash: str) -> DiscMatch:
        raw_type = item.get("type") or ""
        disc_type = TYPE_MAP.get(raw_type, DiscType.UNKNOWN)

        external_ids = item.get("externalids") or {}
        tmdb_id = _parse_int(external_ids.get("tmdb"))

        media_info = MediaInfo(
            title=item.get("title") or "",
            year=item.get("year"),
            disc_type=disc_type,
            tmdb_id=tmdb_id,
            overview="",
            popularity=0.0,
        )

        # `releases[].discs[]` each carry their own contentHash. A multi-disc
        # release (TV box sets, multi-edition movies) can reuse the same
        # sourceFile value across discs, so titles must only be pulled from
        # the disc(s) that actually match the hash that was looked up —
        # otherwise a caller joining by source_file alone can silently pick
        # up another disc's episode/movie data. TheDiscDB compares hashes
        # case-insensitively (confirmed against its live API), so this
        # comparison must too, or a correct server-side match would fail to
        # line up with any disc here and spuriously fall back to all discs.
        all_discs: list[dict] = []
        matched_discs: list[dict] = []
        for release in item.get("releases") or []:
            for disc in release.get("discs") or []:
                all_discs.append(disc)
                if (disc.get("contentHash") or "").lower() == content_hash.lower():
                    matched_discs.append(disc)

        if matched_discs:
            discs_to_use = matched_discs
        else:
            # Unexpected: the GraphQL query already filters server-side by
            # hash, so this should only happen against a lenient/misbehaving
            # server or a response bundling multiple items. Fall back to
            # every disc rather than dropping all titles, but flag it.
            log.warning(
                "No disc in TheDiscDB response matched content hash %r; "
                "falling back to titles from all discs in the response",
                content_hash,
            )
            discs_to_use = all_discs

        titles: list[DiscDBTitle] = []
        for disc in discs_to_use:
            for disc_title in disc.get("titles") or []:
                disc_item = disc_title.get("item") or {}
                titles.append(
                    DiscDBTitle(
                        source_file=disc_title.get("sourceFile") or "",
                        has_item=bool(disc_title.get("hasItem")),
                        title=disc_item.get("title") or "",
                        type=disc_item.get("type") or "",
                        season=_parse_int(disc_item.get("season")),
                        episode=_parse_int(disc_item.get("episode")),
                    )
                )

        return DiscMatch(media_info=media_info, titles=titles)
