import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# TINFO attribute IDs from makemkvcon (verified against live disc output)
_ATTR_NAME = 2
_ATTR_DURATION = 9
_ATTR_CHAPTERS = 8
_ATTR_FILENAME = 27
_ATTR_SIZE = 11  # expected output file size in bytes
_ATTR_SOURCE_FILE = 16  # BDMV playlist/stream filename on the original disc (e.g. "00800.mpls")


@dataclass
class TitleInfo:
    id: int
    name: str
    duration_seconds: int
    filename: str
    chapter_count: int = 0
    expected_bytes: int = 0
    source_file: str = ""

    @property
    def is_main_feature_hint(self) -> bool:
        """MakeMKV flags the main Blu-ray playlist with this marker."""
        return "FPL_MainFeature" in self.name


def _parse_duration(s: str) -> int:
    """Convert HH:MM:SS to total seconds. Returns 0 on parse failure."""
    parts = s.split(":")
    if len(parts) == 3:
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            pass
    return 0


def _disc_index(drive_path: str) -> int:
    """Extract disc index N from a /dev/srN path."""
    m = re.search(r"\d+$", drive_path)
    if m is None:
        raise ValueError(f"Cannot determine disc index from {drive_path!r}")
    return int(m.group())


_SUBPROCESS_SHUTDOWN_TIMEOUT = 10  # seconds to wait after terminate() before escalating to kill()


async def _terminate_and_wait(proc: asyncio.subprocess.Process) -> None:
    """Ensure `proc` is dead before returning -- used from `finally` blocks so
    cancelling a rip (e.g. daemon shutdown) never orphans a running
    makemkvcon process. A no-op if the process has already exited.
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=_SUBPROCESS_SHUTDOWN_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(asyncio.CancelledError):
            await proc.wait()


class Ripper:
    def __init__(
        self,
        drive_path: str,
        min_duration_seconds: int = 1200,
        makemkvcon_path: str = "makemkvcon",
    ) -> None:
        self._drive_path = drive_path
        self._disc_index = _disc_index(drive_path)
        self._min_duration = min_duration_seconds
        self._makemkvcon = makemkvcon_path

    async def get_disc_info(self) -> list[TitleInfo]:
        """Run makemkvcon info and return titles at or above min_duration_seconds."""
        proc = await asyncio.create_subprocess_exec(
            self._makemkvcon, "-r", "info", f"disc:{self._disc_index}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )
        assert proc.stdout is not None

        try:
            raw: dict[int, dict[int, str]] = {}
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("TINFO:"):
                    continue
                parts = line[6:].split(",", 3)
                if len(parts) < 4:
                    continue
                try:
                    title_id, attr_id = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                raw.setdefault(title_id, {})[attr_id] = parts[3].strip('"')

            await proc.wait()
        finally:
            await _terminate_and_wait(proc)

        titles: list[TitleInfo] = []
        for title_id, attrs in sorted(raw.items()):
            duration = _parse_duration(attrs.get(_ATTR_DURATION, "0:0:0"))
            if duration < self._min_duration:
                continue
            titles.append(TitleInfo(
                id=title_id,
                name=attrs.get(_ATTR_NAME, ""),
                duration_seconds=duration,
                filename=attrs.get(_ATTR_FILENAME, f"title_t{title_id:02d}.mkv"),
                chapter_count=int(attrs.get(_ATTR_CHAPTERS, "0") or "0"),
                expected_bytes=int(attrs.get(_ATTR_SIZE, "0") or "0"),
                source_file=attrs.get(_ATTR_SOURCE_FILE, ""),
            ))

        log.debug("Found %d valid title(s) on %s", len(titles), self._drive_path)
        return titles

    def select_main_title(self, titles: list[TitleInfo]) -> TitleInfo:
        """Return the main feature: prefer FPL_MainFeature flag, fallback to longest."""
        if not titles:
            raise ValueError("No valid titles found on disc")
        for t in titles:
            if t.is_main_feature_hint:
                return t
        return max(titles, key=lambda t: t.duration_seconds)

    def is_tv_show_hint(self, titles: list[TitleInfo]) -> bool:
        """True when multiple similar-length titles suggest TV episodes on a season disc."""
        if len(titles) < 2:
            return False
        durations = sorted(t.duration_seconds for t in titles)
        # All titles within 30% of each other → likely episodes
        return durations[0] / durations[-1] > 0.7

    def has_ambiguous_main_feature(self, titles: list[TitleInfo]) -> bool:
        """True when there are multiple titles and none is flagged as the main feature.

        In this situation `select_main_title` would silently fall back to guessing
        "longest duration" — callers should treat this as ambiguous instead of guessing.
        """
        return len(titles) > 1 and not any(t.is_main_feature_hint for t in titles)

    async def rip(
        self,
        title_id: int,
        output_dir: Path,
        on_progress: Callable[[int], Awaitable[None]] | None = None,
        expected_bytes: int = 0,
    ) -> Path:
        """Extract one title to output_dir. Returns path to the largest resulting MKV.

        Progress is reported via PRGV lines when available; otherwise polled from
        output file size against expected_bytes every 5 seconds.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            self._makemkvcon, "-r", "mkv",
            f"disc:{self._disc_index}",
            str(title_id),
            str(output_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )
        assert proc.stdout is not None

        poll_task: asyncio.Task | None = None
        if on_progress is not None and expected_bytes > 0:
            async def _poll_size() -> None:
                while True:
                    await asyncio.sleep(5)
                    written = sum(
                        p.stat().st_size for p in output_dir.glob("*.mkv")
                        if p.exists()
                    )
                    # Cap at 99 — the final 100 is set after process exits cleanly.
                    await on_progress(min(99, written * 100 // expected_bytes))
            poll_task = asyncio.create_task(_poll_size())

        try:
            try:
                async for raw_line in proc.stdout:
                    line = raw_line.decode(errors="replace").strip()
                    if not line.startswith("PRGV:") or on_progress is None:
                        continue
                    parts = line[5:].split(",")
                    if len(parts) == 3:
                        try:
                            current, maximum = int(parts[0]), int(parts[2])
                            if maximum > 0:
                                # PRGV is available — cancel the size poller.
                                if poll_task is not None:
                                    poll_task.cancel()
                                    poll_task = None
                                await on_progress(min(100, current * 100 // maximum))
                        except ValueError:
                            pass
            finally:
                if poll_task is not None:
                    poll_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await poll_task

            returncode = await proc.wait()
        finally:
            # Cancellation (e.g. daemon shutdown) must not orphan makemkvcon.
            await _terminate_and_wait(proc)

        if returncode != 0:
            raise RuntimeError(f"makemkvcon exited with code {returncode}")

        mkv_files = sorted(output_dir.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True)
        if not mkv_files:
            raise RuntimeError(f"No MKV files in {output_dir} after ripping title {title_id}")

        return mkv_files[0]
