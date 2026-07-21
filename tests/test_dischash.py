"""Tests for `jacques.services.dischash`, using synthetic pycdlib images.

There's no real optical drive available in CI/dev, so each test builds a
small disc image on the fly with pycdlib itself (`.new(interchange_level=3,
udf="2.60")` for a Blu-ray-shaped UDF+ISO9660 image with `/BDMV/STREAM`,
`.new(interchange_level=3)` with no `udf=` for a DVD-shaped plain ISO9660
image with `/VIDEO_TS`), writes it out to `tmp_path`, and feeds that file's
path to `compute_content_hash` exactly as a real drive path would be used.
"""

import hashlib
import io
import struct
import subprocess

import pycdlib
from pycdlib import pycdlibexception

from jacques.services.dischash import compute_content_hash


def _expected_hash(files: list[tuple[str, int]]) -> str:
    """Hand-compute the expected ContentHash for a set of (filename, size).

    Mirrors the algorithm under test: sort by filename, MD5 the concatenated
    8-byte little-endian sizes in that order.
    """
    digest = hashlib.md5()
    for _name, size in sorted(files, key=lambda entry: entry[0]):
        digest.update(struct.pack("<Q", size))
    return digest.hexdigest()


def _build_bd_image(path: str, files: list[tuple[str, int]]) -> None:
    """Write a Blu-ray-shaped UDF+ISO9660 image with BDMV/STREAM/<name>.

    `files` is a list of (udf filename, size in bytes); each file's content
    is filler bytes of the requested length. The implementation reads the
    UDF view, so the ISO9660 side just needs distinct 8.3-shaped names.
    """
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, udf="2.60")
    iso.add_directory(iso_path="/BDMV", udf_path="/BDMV")
    iso.add_directory(iso_path="/BDMV/STREAM", udf_path="/BDMV/STREAM")

    for index, (name, size) in enumerate(files):
        data = bytes([index % 256]) * size
        iso.add_fp(
            io.BytesIO(data),
            len(data),
            iso_path=f"/BDMV/STREAM/F{index}.DAT;1",
            udf_path=f"/BDMV/STREAM/{name}",
        )

    iso.write(path)
    iso.close()


def _build_dvd_image(path: str, files: list[tuple[str, int]]) -> None:
    """Write a DVD-shaped plain ISO9660 image with VIDEO_TS/<name>.

    `files` is a list of (iso9660 filename, size in bytes); pycdlib appends
    the ISO9660 ";1" version suffix to each identifier automatically.
    """
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3)
    iso.add_directory(iso_path="/VIDEO_TS")

    for index, (name, size) in enumerate(files):
        data = bytes([index % 256]) * size
        iso.add_fp(io.BytesIO(data), len(data), iso_path=f"/VIDEO_TS/{name};1")

    iso.write(path)
    iso.close()


def _build_plain_image(path: str) -> None:
    """Write a valid ISO9660 image with neither BDMV/STREAM nor VIDEO_TS."""
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3)
    iso.add_directory(iso_path="/OTHERDIR")
    iso.write(path)
    iso.close()


# ── Blu-ray path ──────────────────────────────────────────────────────────────


def test_blu_ray_image_hash_matches_hand_computed(tmp_path):
    files = [("00002.m2ts", 8192), ("00001.m2ts", 4096), ("00003.m2ts", 2048)]
    image_path = tmp_path / "bd.iso"
    _build_bd_image(str(image_path), files)

    result = compute_content_hash(str(image_path))

    assert result == _expected_hash(files)


def test_blu_ray_image_returns_lowercase_hex_digest(tmp_path):
    files = [("00001.m2ts", 1024)]
    image_path = tmp_path / "bd_single.iso"
    _build_bd_image(str(image_path), files)

    result = compute_content_hash(str(image_path))

    assert result is not None
    assert len(result) == 32
    assert result == result.lower()
    int(result, 16)  # raises ValueError if not valid hex


# ── DVD path ──────────────────────────────────────────────────────────────────


def test_dvd_image_hash_matches_hand_computed(tmp_path):
    files = [("VTS_01_1.VOB", 6144), ("VIDEO_TS.IFO", 2048), ("VTS_01_0.IFO", 512)]
    image_path = tmp_path / "dvd.iso"
    _build_dvd_image(str(image_path), files)

    result = compute_content_hash(str(image_path))

    assert result == _expected_hash(files)


def test_dvd_image_strips_iso9660_version_suffix_before_sorting(tmp_path):
    """Confirm the ";N" version suffix is stripped before sorting, not a no-op.

    "AAAA" is a strict prefix of "AAAA1". Sorted with the raw ";1" suffix
    still attached, the byte ';' (0x3B) compares *greater* than the digit
    '1' (0x31), so "AAAA1;1" sorts *before* "AAAA;1" -- the opposite of the
    correct stripped-name order ("AAAA" < "AAAA1"). Since the two files have
    different sizes, hashing them in the wrong order produces a different
    digest, so this genuinely exercises the stripping rather than
    accidentally passing either way.
    """
    files = [("AAAA", 100), ("AAAA1", 200)]
    image_path = tmp_path / "dvd_suffix.iso"
    _build_dvd_image(str(image_path), files)

    result = compute_content_hash(str(image_path))

    stripped_order_hash = _expected_hash(files)
    raw_suffix_order_hash = hashlib.md5(
        struct.pack("<Q", 200) + struct.pack("<Q", 100)
    ).hexdigest()

    assert stripped_order_hash != raw_suffix_order_hash  # sanity: orders truly differ
    assert result == stripped_order_hash
    assert result != raw_suffix_order_hash


# ── Failure paths: never raise, always None ──────────────────────────────────


def test_nonexistent_path_returns_none(tmp_path):
    missing = tmp_path / "does_not_exist.iso"

    assert compute_content_hash(str(missing)) is None


def test_non_iso_file_returns_none(tmp_path):
    not_an_iso = tmp_path / "notes.txt"
    not_an_iso.write_text("just a small text file, not an ISO9660/UDF image")

    assert compute_content_hash(str(not_an_iso)) is None


def test_image_without_bdmv_or_video_ts_returns_none(tmp_path):
    image_path = tmp_path / "plain.iso"
    _build_plain_image(str(image_path))

    assert compute_content_hash(str(image_path)) is None


# ── Pure-UDF fallback (no ISO9660 PVD at all) ─────────────────────────────────
#
# pycdlib's own `.new()` always writes a valid ISO9660 PVD, even for `udf=`
# images, so a "real pure-UDF" disc can't be reproduced with a genuine
# pycdlib-authored image. These tests instead monkeypatch `PyCdlib.open` to
# raise exactly what pycdlib raises for a real disc like this (confirmed
# against an actual Blu-ray with no ISO9660 bridge layer), and fake the `7zz`
# subprocess call to emit `-slt`-formatted output for the requested
# directory, exactly as `_compute_content_hash_via_7z` would parse it from a
# real `7zz l -slt` invocation (format also confirmed against that same real
# disc).


def _fake_open_pure_udf(self, filename, mode="rb"):
    self._has_udf = True
    raise pycdlibexception.PyCdlibInvalidISO("Valid ISO9660 filesystems must have at least one PVD")


def _fake_open_not_a_disc(self, filename, mode="rb"):
    raise pycdlibexception.PyCdlibInvalidISO("Failed to read entire volume descriptor")


def _7z_slt_block(path: str, size: int) -> str:
    """One `7zz l -slt` block for a (non-directory) file entry."""
    return f"Path = {path}\nFolder = -\nSize = {size}\nPacked Size = {size}\n"


def _fake_7z_listing(dir_to_files: dict[str, list[tuple[str, int]]]):
    """Build a fake `subprocess.run` that emits `-slt`-formatted output for `7zz`."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "7zz":
            requested = cmd[-1].removesuffix("/*")
            files = dir_to_files.get(requested, [])
            stdout = "\n".join(_7z_slt_block(f"{requested}/{name}", size) for name, size in files)
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0)

    return fake_run


def test_pure_udf_disc_falls_back_to_7z(tmp_path, monkeypatch):
    monkeypatch.setattr(pycdlib.PyCdlib, "open", _fake_open_pure_udf)
    files = [("00002.m2ts", 8192), ("00001.m2ts", 4096), ("00003.m2ts", 2048)]
    monkeypatch.setattr(
        "jacques.services.dischash.subprocess.run",
        _fake_7z_listing({"BDMV/STREAM": files}),
    )

    result = compute_content_hash(str(tmp_path / "disc.img"))

    assert result == _expected_hash(files)


def test_pure_udf_dvd_shaped_disc_falls_back_to_7z(tmp_path, monkeypatch):
    monkeypatch.setattr(pycdlib.PyCdlib, "open", _fake_open_pure_udf)
    files = [("VTS_01_1.VOB", 6144), ("VIDEO_TS.IFO", 2048)]
    monkeypatch.setattr(
        "jacques.services.dischash.subprocess.run",
        _fake_7z_listing({"VIDEO_TS": files}),
    )

    result = compute_content_hash(str(tmp_path / "disc.img"))

    assert result == _expected_hash(files)


def test_pure_udf_disc_without_bdmv_or_video_ts_after_7z_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pycdlib.PyCdlib, "open", _fake_open_pure_udf)
    monkeypatch.setattr("jacques.services.dischash.subprocess.run", _fake_7z_listing({}))

    assert compute_content_hash(str(tmp_path / "disc.img")) is None


def test_pure_udf_disc_skips_directory_entries_via_7z(tmp_path, monkeypatch):
    """A `Folder = +` block (a subdirectory) must never be hashed as a file."""
    monkeypatch.setattr(pycdlib.PyCdlib, "open", _fake_open_pure_udf)
    files = [("00001.m2ts", 4096)]

    def fake_run(cmd, **kwargs):
        stdout = (
            "Path = BDMV/STREAM/SUBDIR\nFolder = +\nSize = 0\n\n"
            + _7z_slt_block("BDMV/STREAM/00001.m2ts", 4096)
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("jacques.services.dischash.subprocess.run", fake_run)

    result = compute_content_hash(str(tmp_path / "disc.img"))

    assert result == _expected_hash(files)


def test_non_disc_invalid_iso_does_not_attempt_7z(tmp_path, monkeypatch):
    """A file that's just not a disc image at all must never shell out to 7zz."""
    monkeypatch.setattr(pycdlib.PyCdlib, "open", _fake_open_not_a_disc)

    def fail_if_called(cmd, **kwargs):
        raise AssertionError(f"7zz should not be attempted for a non-disc file: {cmd}")

    monkeypatch.setattr("jacques.services.dischash.subprocess.run", fail_if_called)

    assert compute_content_hash(str(tmp_path / "garbage.bin")) is None


def test_7z_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pycdlib.PyCdlib, "open", _fake_open_pure_udf)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("7zz: command not found")

    monkeypatch.setattr("jacques.services.dischash.subprocess.run", fake_run)

    assert compute_content_hash(str(tmp_path / "disc.img")) is None
