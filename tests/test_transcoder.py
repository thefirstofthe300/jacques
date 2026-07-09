from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jacques.services.transcoder import Transcoder


async def _async_lines(lines: list[bytes]):
    for line in lines:
        yield line


@pytest.mark.asyncio
async def test_transcode_parses_progress(tmp_path):
    lines = [
        b'Progress: {\n',
        b'"State": "WORKING", "Working": {"Progress": 0.25}\n',
        b'}\n',
        b'Progress: {\n',
        b'"State": "WORKING", "Working": {"Progress": 0.75}\n',
        b'}\n',
        b'Progress: {\n',
        b'"State": "WORKING", "Working": {"Progress": 1.0}\n',
        b'}\n',
    ]
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    captured: list[int] = []

    async def on_progress(pct: int) -> None:
        captured.append(pct)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        await Transcoder(quality=20).transcode(
            tmp_path / "in.mkv",
            tmp_path / "out.mkv",
            on_progress=on_progress,
        )

    assert captured == [25, 75, 100]


@pytest.mark.asyncio
async def test_transcode_creates_output_parent(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=0)

    output = tmp_path / "deep" / "nested" / "out.mkv"

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        await Transcoder().transcode(tmp_path / "in.mkv", output)

    assert output.parent.exists()


@pytest.mark.asyncio
async def test_transcode_no_progress_callback_does_not_raise(tmp_path):
    lines = [
        b"Encoding: task 1 of 1, 50.00 % (100.00 fps)\n",
    ]
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")


@pytest.mark.asyncio
async def test_transcode_raises_on_nonzero_exit(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=1)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="HandBrakeCLI exited with code 1"):
            await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")


@pytest.mark.asyncio
async def test_transcode_uses_encoder_preset(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=0)

    mock_exec = AsyncMock(return_value=mock_proc)
    with patch("asyncio.create_subprocess_exec", mock_exec):
        await Transcoder(preset="slow").transcode(
            tmp_path / "in.mkv", tmp_path / "out.mkv"
        )

    call_args = mock_exec.call_args.args
    assert "--encoder-preset" in call_args
    assert "slow" in call_args


@pytest.mark.asyncio
async def test_transcode_uses_default_encoder_preset(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=0)

    mock_exec = AsyncMock(return_value=mock_proc)
    with patch("asyncio.create_subprocess_exec", mock_exec):
        await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")

    call_args = mock_exec.call_args.args
    assert "--encoder-preset" in call_args
    assert "medium" in call_args


@pytest.mark.asyncio
async def test_transcode_clamps_progress_to_100(tmp_path):
    lines = [
        b"Encoding: task 1 of 1, 101.50 % (100.00 fps)\n",
    ]
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    captured: list[int] = []

    async def on_progress(pct: int) -> None:
        captured.append(pct)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        await Transcoder().transcode(
            tmp_path / "in.mkv", tmp_path / "out.mkv", on_progress=on_progress
        )

    assert all(p <= 100 for p in captured)
