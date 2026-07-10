from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jacques.services.transcoder import Transcoder


async def _async_lines(lines: list[bytes]):
    for line in lines:
        yield line


def _mock_proc(stdout_lines=None, returncode=0, stderr_data=b""):
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(stdout_lines or [])
    mock_proc.returncode = returncode
    mock_proc.stderr.read = AsyncMock(return_value=stderr_data)
    mock_proc.wait = AsyncMock(return_value=returncode)
    return mock_proc


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
    mock_proc = _mock_proc(stdout_lines=lines)

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
    output = tmp_path / "deep" / "nested" / "out.mkv"

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_mock_proc())):
        await Transcoder().transcode(tmp_path / "in.mkv", output)

    assert output.parent.exists()


@pytest.mark.asyncio
async def test_transcode_no_progress_callback_does_not_raise(tmp_path):
    lines = [
        b"Encoding: task 1 of 1, 50.00 % (100.00 fps)\n",
    ]
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_mock_proc(stdout_lines=lines))):
        await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")


@pytest.mark.asyncio
async def test_transcode_raises_on_nonzero_exit(tmp_path):
    mock_proc = _mock_proc(returncode=1, stderr_data=b"Error: cannot open source file")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="HandBrakeCLI exited with code 1: Error: cannot open source file"):
            await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")


@pytest.mark.asyncio
async def test_transcode_uses_encoder_preset(tmp_path):
    mock_exec = AsyncMock(return_value=_mock_proc())
    with patch("asyncio.create_subprocess_exec", mock_exec):
        await Transcoder(preset="slow").transcode(
            tmp_path / "in.mkv", tmp_path / "out.mkv"
        )

    call_args = mock_exec.call_args.args
    assert "--encoder-preset" in call_args
    assert "slow" in call_args


@pytest.mark.asyncio
async def test_transcode_uses_default_encoder_preset(tmp_path):
    mock_exec = AsyncMock(return_value=_mock_proc())
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
    captured: list[int] = []

    async def on_progress(pct: int) -> None:
        captured.append(pct)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_mock_proc(stdout_lines=lines))):
        await Transcoder().transcode(
            tmp_path / "in.mkv", tmp_path / "out.mkv", on_progress=on_progress
        )

    assert all(p <= 100 for p in captured)


@pytest.mark.asyncio
async def test_transcode_treats_255_with_encode_done_as_success(tmp_path):
    # HandBrakeCLI 1.10.2 exits 255 even on success on some Linux packaging setups.
    stderr = b"x265 [info]: ...\nEncode done!\nHandBrake has exited."
    mock_proc = _mock_proc(returncode=255, stderr_data=stderr)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")


@pytest.mark.asyncio
async def test_transcode_raises_on_255_without_encode_done(tmp_path):
    mock_proc = _mock_proc(returncode=255, stderr_data=b"Error: cannot open source file")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="HandBrakeCLI exited with code 255"):
            await Transcoder().transcode(tmp_path / "in.mkv", tmp_path / "out.mkv")
