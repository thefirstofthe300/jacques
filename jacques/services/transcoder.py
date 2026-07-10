import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)


class Transcoder:
    def __init__(self, quality: int = 20, handbrake_path: str = "HandBrakeCLI", preset: str = "medium") -> None:
        self._quality = quality
        self._handbrake = handbrake_path
        self._preset = preset

    async def transcode(
        self,
        input_path: Path,
        output_path: Path,
        on_progress: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        """Convert input MKV to H.265/HEVC using HandBrakeCLI.

        --json writes multi-line JSON blocks to stdout (stderr is human-readable text).
        Each block starts with "Type: {" and ends with a bare "}".
        Progress is reported as Working.Progress (float 0–1).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._handbrake,
            "--json",
            "-i", str(input_path),
            "-o", str(output_path),
            "--encoder", "x265",
            "--quality", str(self._quality),
            "--encoder-preset", self._preset,
            "--audio-lang-list", "und",
            "--all-audio",
            "--aencoder", "copy",
            "--subtitle-lang-list", "und",
            "--all-subtitles",
        ]
        log.debug("%s: %s", self._handbrake, " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        stderr_task = asyncio.create_task(proc.stderr.read())

        try:
            buf: list[str] = []
            async for raw_line in proc.stdout:
                if on_progress is None:
                    continue
                line = raw_line.decode(errors="replace").rstrip("\n")
                if not buf:
                    if line.startswith("Progress: {"):
                        buf.append(line[len("Progress: "):])
                else:
                    buf.append(line)
                    if line == "}":
                        try:
                            data = json.loads("\n".join(buf))
                            if data.get("State") == "WORKING":
                                pct = data.get("Working", {}).get("Progress", 0)
                                await on_progress(min(100, int(pct * 100)))
                        except (ValueError, TypeError):
                            pass
                        buf = []
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
            await asyncio.gather(proc.wait(), stderr_task, return_exceptions=True)

        if proc.returncode != 0:
            stderr_out = ""
            if stderr_task.done() and not stderr_task.cancelled():
                with contextlib.suppress(Exception):
                    stderr_out = stderr_task.result().decode(errors="replace").strip()
            # HandBrakeCLI 1.10.2 exits with 255 even on a successful encode on some
            # Linux packaging setups. "Encode done!" only appears after a real completion.
            if proc.returncode == 255 and "Encode done!" in stderr_out:
                log.warning("HandBrakeCLI exited 255 but encode succeeded; treating as success")
                return
            detail = f": {stderr_out}" if stderr_out else ""
            raise RuntimeError(f"HandBrakeCLI exited with code {proc.returncode}{detail}")
