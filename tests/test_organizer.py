from pathlib import Path

import pytest

from jacques.models.job import DiscType
from jacques.services.metadata import MediaInfo
from jacques.services.organizer import Organizer, _safe_name


# ── _safe_name ────────────────────────────────────────────────────────────────


def test_safe_name_replaces_colon():
    assert _safe_name("Mission: Impossible") == "Mission_ Impossible"


def test_safe_name_replaces_slash():
    assert _safe_name("AC/DC Live") == "AC_DC Live"


def test_safe_name_replaces_quotes():
    assert _safe_name('She Said "Yes"') == "She Said _Yes_"


def test_safe_name_strips_whitespace():
    assert _safe_name("  Padded  ") == "Padded"


def test_safe_name_multiple_bad_chars():
    assert _safe_name('Title: "Sub/Title"') == "Title_ _Sub_Title_"


# ── build_destination ─────────────────────────────────────────────────────────


def test_build_destination_movie_with_year(tmp_path):
    org = Organizer(tmp_path)
    info = MediaInfo("Inception", 2010, DiscType.MOVIE, 123)
    dest = org.build_destination(info, "INCEPTION")
    assert dest == tmp_path / "Movies" / "Inception (2010)" / "Inception (2010).mkv"


def test_build_destination_movie_without_year(tmp_path):
    org = Organizer(tmp_path)
    info = MediaInfo("Unknown Film", None, DiscType.MOVIE, None)
    dest = org.build_destination(info, None)
    assert dest == tmp_path / "Movies" / "Unknown Film" / "Unknown Film.mkv"


def test_build_destination_tv_show(tmp_path):
    org = Organizer(tmp_path)
    info = MediaInfo("Breaking Bad", 2008, DiscType.TV_SHOW, 456)
    dest = org.build_destination(info, "BREAKING_BAD", episode_num=3)
    expected = (
        tmp_path / "TV Shows" / "Breaking Bad (2008)" / "Season 01" / "Breaking Bad - S01E03.mkv"
    )
    assert dest == expected


def test_build_destination_tv_default_episode_num(tmp_path):
    org = Organizer(tmp_path)
    info = MediaInfo("Sopranos", 1999, DiscType.TV_SHOW, 999)
    dest = org.build_destination(info, None)
    assert "S01E01" in dest.name


def test_build_destination_no_metadata_uses_disc_label(tmp_path):
    org = Organizer(tmp_path)
    dest = org.build_destination(None, "MY_DISC")
    assert dest == tmp_path / "Unknown" / "MY_DISC.mkv"


def test_build_destination_no_metadata_no_label(tmp_path):
    org = Organizer(tmp_path)
    dest = org.build_destination(None, None)
    assert dest == tmp_path / "Unknown" / "unknown.mkv"


def test_build_destination_sanitizes_title(tmp_path):
    org = Organizer(tmp_path)
    info = MediaInfo("Mission: Impossible", 1996, DiscType.MOVIE, 1)
    dest = org.build_destination(info, None)
    assert "Mission_ Impossible (1996)" in str(dest)
    assert ":" not in str(dest)


# ── move ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_move_creates_parent_directories(tmp_path):
    src = tmp_path / "source.mkv"
    src.write_bytes(b"video data")
    dest = tmp_path / "Movies" / "Film (2024)" / "Film (2024).mkv"

    await Organizer(tmp_path).move(src, dest)

    assert dest.exists()
    assert dest.read_bytes() == b"video data"
    assert not src.exists()


@pytest.mark.asyncio
async def test_move_overwrites_existing_file(tmp_path):
    src = tmp_path / "new.mkv"
    src.write_bytes(b"new content")
    dest = tmp_path / "out.mkv"
    dest.write_bytes(b"old content")

    await Organizer(tmp_path).move(src, dest)

    assert dest.read_bytes() == b"new content"
    assert not src.exists()
