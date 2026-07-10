from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jacques.services.ripper import Ripper, TitleInfo, _disc_index, _parse_duration


# ── pure unit tests (no I/O) ──────────────────────────────────────────────────


def test_parse_duration_standard():
    assert _parse_duration("1:45:00") == 6300
    assert _parse_duration("0:20:00") == 1200
    assert _parse_duration("2:00:00") == 7200


def test_parse_duration_edge_cases():
    assert _parse_duration("0:00:00") == 0
    assert _parse_duration("bad") == 0
    assert _parse_duration("") == 0


def test_disc_index_extracts_number():
    assert _disc_index("/dev/sr0") == 0
    assert _disc_index("/dev/sr1") == 1
    assert _disc_index("/dev/sr12") == 12


def test_disc_index_raises_on_non_numeric():
    with pytest.raises(ValueError, match="Cannot determine disc index"):
        _disc_index("/dev/cdrom")


def test_select_main_title_prefers_fpl_flag():
    titles = [
        TitleInfo(0, "Title (FPL_MainFeature)", 7200, "t00.mkv"),
        TitleInfo(1, "Other Title", 9000, "t01.mkv"),  # longer, but not flagged
    ]
    assert Ripper("/dev/sr0").select_main_title(titles).id == 0


def test_select_main_title_falls_back_to_longest():
    titles = [
        TitleInfo(0, "Short Feature", 3600, "t00.mkv"),
        TitleInfo(1, "Long Feature", 7200, "t01.mkv"),
    ]
    assert Ripper("/dev/sr0").select_main_title(titles).id == 1


def test_select_main_title_raises_on_empty():
    with pytest.raises(ValueError, match="No valid titles"):
        Ripper("/dev/sr0").select_main_title([])


def test_is_tv_show_hint_single_title():
    titles = [TitleInfo(0, "Movie", 7200, "t00.mkv")]
    assert not Ripper("/dev/sr0").is_tv_show_hint(titles)


def test_is_tv_show_hint_similar_durations():
    titles = [
        TitleInfo(0, "Episode 1", 2580, "t00.mkv"),
        TitleInfo(1, "Episode 2", 2700, "t01.mkv"),
        TitleInfo(2, "Episode 3", 2640, "t02.mkv"),
    ]
    assert Ripper("/dev/sr0").is_tv_show_hint(titles)


def test_is_tv_show_hint_dissimilar_durations():
    # 1500 / 7200 ≈ 0.21 — well below 0.7 threshold
    titles = [
        TitleInfo(0, "Movie", 7200, "t00.mkv"),
        TitleInfo(1, "Short Extra", 1500, "t01.mkv"),
    ]
    assert not Ripper("/dev/sr0").is_tv_show_hint(titles)


def test_has_ambiguous_main_feature_single_title():
    titles = [TitleInfo(0, "Movie", 7200, "t00.mkv")]
    assert not Ripper("/dev/sr0").has_ambiguous_main_feature(titles)


def test_has_ambiguous_main_feature_one_flagged():
    titles = [
        TitleInfo(0, "Title (FPL_MainFeature)", 7200, "t00.mkv"),
        TitleInfo(1, "Other Title", 6900, "t01.mkv"),
    ]
    assert not Ripper("/dev/sr0").has_ambiguous_main_feature(titles)


def test_has_ambiguous_main_feature_none_flagged():
    titles = [
        TitleInfo(0, "Title A", 7200, "t00.mkv"),
        TitleInfo(1, "Title B", 6900, "t01.mkv"),
    ]
    assert Ripper("/dev/sr0").has_ambiguous_main_feature(titles)


def test_has_ambiguous_main_feature_empty_list():
    assert not Ripper("/dev/sr0").has_ambiguous_main_feature([])


# ── async helpers ─────────────────────────────────────────────────────────────


async def _async_lines(lines: list[bytes]):
    for line in lines:
        yield line


# ── get_disc_info tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_disc_info_parses_valid_title():
    lines = [
        b'TINFO:0,2,0,"Feature Film"\n',
        b'TINFO:0,9,0,"1:45:00"\n',
        b'TINFO:0,8,0,"24"\n',
        b'TINFO:0,11,0,"1000000000"\n',
        b'TINFO:0,27,0,"title_t00.mkv"\n',
    ]
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        titles = await Ripper("/dev/sr0", min_duration_seconds=1200).get_disc_info()

    assert len(titles) == 1
    t = titles[0]
    assert t.name == "Feature Film"
    assert t.duration_seconds == 6300
    assert t.filename == "title_t00.mkv"
    assert t.chapter_count == 24


@pytest.mark.asyncio
async def test_get_disc_info_filters_short_titles():
    lines = [
        b'TINFO:0,2,0,"Main Feature"\n',
        b'TINFO:0,9,0,"1:45:00"\n',  # 6300s — kept
        b'TINFO:0,11,0,"0"\n',
        b'TINFO:1,2,0,"Trailer"\n',
        b'TINFO:1,9,0,"0:02:30"\n',  # 150s — filtered
        b'TINFO:1,11,0,"0"\n',
    ]
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        titles = await Ripper("/dev/sr0", min_duration_seconds=1200).get_disc_info()

    assert len(titles) == 1
    assert titles[0].name == "Main Feature"


@pytest.mark.asyncio
async def test_get_disc_info_ignores_non_tinfo_lines():
    lines = [
        b"MSG:5010,0,1,ignored\n",
        b'CINFO:1,0,"some disc info"\n',
        b'TINFO:0,2,0,"The Film"\n',
        b'TINFO:0,9,0,"2:00:00"\n',
        b'TINFO:0,11,0,"0"\n',
    ]
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        titles = await Ripper("/dev/sr0", min_duration_seconds=1200).get_disc_info()

    assert len(titles) == 1


# ── rip tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rip_calls_progress_callback(tmp_path):
    lines = [
        b"PRGV:500,0,1000\n",
        b"PRGV:1000,0,1000\n",
    ]
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "title_t00.mkv").write_bytes(b"x" * 1000)

    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines(lines)
    mock_proc.wait = AsyncMock(return_value=0)

    captured: list[int] = []

    async def on_progress(pct: int) -> None:
        captured.append(pct)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        result = await Ripper("/dev/sr0").rip(0, raw_dir, on_progress=on_progress)

    assert captured == [50, 100]
    assert result == raw_dir / "title_t00.mkv"


@pytest.mark.asyncio
async def test_rip_returns_largest_mkv(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "small.mkv").write_bytes(b"x" * 100)
    (raw_dir / "large.mkv").write_bytes(b"x" * 10_000)

    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        result = await Ripper("/dev/sr0").rip(0, raw_dir)

    assert result.name == "large.mkv"


@pytest.mark.asyncio
async def test_rip_raises_on_nonzero_exit(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=1)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="makemkvcon exited with code 1"):
            await Ripper("/dev/sr0").rip(0, tmp_path / "raw")


@pytest.mark.asyncio
async def test_rip_raises_when_no_mkv_produced(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    mock_proc = MagicMock()
    mock_proc.stdout = _async_lines([])
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="No MKV files"):
            await Ripper("/dev/sr0").rip(0, raw_dir)
