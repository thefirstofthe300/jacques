import asyncio
import logging
import shutil
from pathlib import Path

from ..models.job import DiscType
from .metadata import MediaInfo

log = logging.getLogger(__name__)

_UNSAFE_CHARS = r'\/:*?"<>|'


def _safe_name(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    for ch in _UNSAFE_CHARS:
        name = name.replace(ch, "_")
    return name.strip()


class Organizer:
    def __init__(self, output_path: Path) -> None:
        self._output_path = output_path

    def build_destination(
        self,
        media_info: MediaInfo | None,
        disc_label: str | None,
        episode_num: int = 1,
        season_num: int = 1,
        episode_title: str | None = None,
    ) -> Path:
        """Return the canonical destination path for a single output file.

        Follows Plex/Jellyfin naming conventions:
          Movies/Title (YYYY)/Title (YYYY).mkv
          TV Shows/Title (YYYY)/Season 01/Title - S01E01.mkv
          TV Shows/Title (YYYY)/Season 01/Title - S01E01 - Episode Title.mkv
          Unknown/<disc_label>.mkv
        """
        if media_info is None:
            label = _safe_name(disc_label or "unknown")
            return self._output_path / "Unknown" / f"{label}.mkv"

        year_suffix = f" ({media_info.year})" if media_info.year else ""
        title = _safe_name(media_info.title)

        if media_info.disc_type == DiscType.MOVIE:
            folder = self._output_path / "Movies" / f"{title}{year_suffix}"
            return folder / f"{title}{year_suffix}.mkv"

        folder = (
            self._output_path
            / "TV Shows"
            / f"{title}{year_suffix}"
            / f"Season {season_num:02d}"
        )
        episode_code = f"{title} - S{season_num:02d}E{episode_num:02d}"
        if episode_title:
            episode_code = f"{episode_code} - {_safe_name(episode_title)}"
        return folder / f"{episode_code}.mkv"

    async def move(self, source: Path, destination: Path) -> None:
        """Move source to destination, creating parent directories as needed.

        Uses asyncio.to_thread because shutil.move copies bytes when crossing
        filesystem boundaries, which can block for several seconds on large files.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            log.warning("Destination already exists, overwriting: %s", destination)
        await asyncio.to_thread(shutil.move, str(source), str(destination))
        log.info("Organized: %s → %s", source.name, destination)
