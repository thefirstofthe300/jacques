"""TheDiscDB "ContentHash" disc fingerprinting.

Computes a content-based fingerprint for an optical disc by hashing the
sizes of its video stream files, in filename order. The algorithm (verified
against TheDiscDB's own ImportBuddy tool and third-party reimplementations
arm-sharp, mkv-mapper, and rip-tui) is:

1. Blu-ray: enumerate every ``*.m2ts`` file under ``BDMV/STREAM/``.
   DVD: enumerate every file under ``VIDEO_TS/`` (no extension filtering).
2. Sort the matched files by filename (plain ordinal string sort).
3. Feed each file's size, as an 8-byte little-endian unsigned integer, into
   an MD5 hash object in that sorted order.
4. Return the hex digest.

This is a best-effort identification aid, not a critical-path operation:
``compute_content_hash`` never raises. Any failure (corrupt disc, wrong
filesystem type, drive not ready, permission error, ...) is logged as a
warning and yields ``None``, so callers always have a clean fallback.

pycdlib requires a valid ISO9660 Primary Volume Descriptor to open anything
at all, even a disc it only needs to read via the UDF view. Many real
Blu-ray discs are mastered as pure UDF with no ISO9660 bridge layer, so
pycdlib can't open them. When that happens -- detected by pycdlib itself
having found UDF content before failing on the missing PVD -- this module
falls back to `7zz` (7-Zip's CLI, which has a built-in UDF reader), parsing
UDF structures directly off the raw device/image rather than relying on an
ISO9660 bridge layer. It opens the device read-only, so this needs no
mount() call and no elevated capability. Neither path ever touches
the AACS/BD+-encrypted stream content itself: file sizes are plain
UDF/ISO9660 filesystem metadata.
"""

import hashlib
import logging
import struct
import subprocess

import pycdlib
from pycdlib import pycdlibexception

log = logging.getLogger(__name__)

_BDMV_STREAM_UDF_PATH = "/BDMV/STREAM"
_VIDEO_TS_ISO_PATH = "/VIDEO_TS"
_M2TS_SUFFIX = ".m2ts"

_7Z_TIMEOUT_SECONDS = 30


def compute_content_hash(drive_path: str) -> str | None:
    """Compute TheDiscDB ContentHash for the disc at `drive_path`.

    Opens the raw block device (or image file) at `drive_path` with pycdlib
    and looks for BDMV/STREAM (Blu-ray, via the UDF filesystem view) or
    VIDEO_TS (DVD, via the ISO9660 view) content, trying Blu-ray first since
    Blu-ray discs are commonly mastered as "UDF Bridge" (both UDF and
    ISO9660) while DVDs are pure ISO9660. Disc format is auto-detected this
    way rather than taken as a parameter, since Jacques's own `DiscType`
    (movie/tv_show/unknown) encodes content type, not physical disc format.

    Falls back to `7zz` (see `_compute_content_hash_via_7z`) when the disc
    has UDF content but no ISO9660 PVD at all, which pycdlib cannot open
    through its normal API.

    Never raises. Returns None (after logging a warning) if the disc can't
    be opened, has neither directory, or anything else goes wrong.
    """
    try:
        return _compute_content_hash(drive_path)
    except Exception:
        log.warning("Failed to compute disc content hash for %s", drive_path, exc_info=True)
        return None


def _compute_content_hash(drive_path: str) -> str | None:
    iso = pycdlib.PyCdlib()
    try:
        iso.open(drive_path)
    except pycdlibexception.PyCdlibInvalidISO:
        # `_has_udf` is set by pycdlib as soon as it recognizes UDF volume
        # structures, before it goes on to fail on the missing PVD -- so this
        # distinguishes "real pure-UDF disc" from "not a disc image at all".
        if not iso._has_udf:  # noqa: SLF001 -- no public way to inspect this after a failed open()
            raise
        log.info(
            "%s has UDF content but no ISO9660 volume descriptor; falling back to "
            "7zz to compute its content hash",
            drive_path,
        )
        return _compute_content_hash_via_7z(drive_path)

    try:
        files = _list_bdmv_stream(iso) or _list_video_ts(iso)
        if not files:
            log.warning(
                "No BDMV/STREAM or VIDEO_TS content found on %s; cannot compute content hash",
                drive_path,
            )
            return None
        return _hash_file_sizes(files)
    finally:
        iso.close()


def _compute_content_hash_via_7z(drive_path: str) -> str | None:
    """Read BDMV/STREAM or VIDEO_TS file sizes via `7zz`, without mounting.

    Used when `drive_path` has UDF content but no ISO9660 PVD, which pycdlib
    cannot open regardless of the UDF content being present. `wrudf` (the
    other no-mount option investigated) turned out to be built around
    CD-R/RW *packet-writing* semantics and refuses to initialize against a
    real pressed disc at all ("CDR disc last track reserved or not variable
    packet"). 7-Zip's own UDF reader has no such assumption -- it parses the
    filesystem directly off the raw device/image and opens it read-only, so
    no mount() and no elevated capability are needed. Confirmed against a
    real pure-UDF Blu-ray (no ISO9660 bridge layer).
    """
    files = _7z_list_dir(drive_path, "BDMV/STREAM", _M2TS_SUFFIX) or _7z_list_dir(drive_path, "VIDEO_TS", None)
    if not files:
        log.warning(
            "No BDMV/STREAM or VIDEO_TS content found on %s via 7zz; cannot compute content hash",
            drive_path,
        )
        return None
    return _hash_file_sizes(files)


def _hash_file_sizes(files: list[tuple[str, int]]) -> str:
    """MD5 of each file's size, as an 8-byte little-endian uint, sorted by filename."""
    digest = hashlib.md5()
    for _name, size in sorted(files, key=lambda entry: entry[0]):
        digest.update(struct.pack("<Q", size))
    return digest.hexdigest()


def _7z_list_dir(drive_path: str, udf_dir: str, suffix: str | None) -> list[tuple[str, int]] | None:
    """Return (filename, size) for every file directly under `udf_dir`, or None.

    Shells out to `7zz l -slt`, which parses the UDF filesystem directly off
    the raw device/image and prints one blank-line-separated block per
    entry (`Path = ...`, `Folder = +/-`, `Size = ...`, plus other fields we
    don't need). Directories (`Folder = +`) are skipped since only file
    sizes feed the hash.
    """
    result = subprocess.run(
        ["7zz", "l", "-slt", drive_path, "--", f"{udf_dir}/*"],
        capture_output=True,
        text=True,
        timeout=_7Z_TIMEOUT_SECONDS,
    )

    files: list[tuple[str, int]] = []
    for block in result.stdout.split("\n\n"):
        path = size = None
        is_dir = False
        for line in block.splitlines():
            if line.startswith("Path = "):
                path = line.removeprefix("Path = ")
            elif line.startswith("Folder = "):
                is_dir = line.removeprefix("Folder = ").strip() == "+"
            elif line.startswith("Size = "):
                size = int(line.removeprefix("Size = "))
        if path is None or is_dir or size is None:
            continue
        name = path.rsplit("/", 1)[-1]
        if suffix is not None and not name.lower().endswith(suffix):
            continue
        files.append((name, size))
    return files or None


def _list_bdmv_stream(iso: "pycdlib.PyCdlib") -> list[tuple[str, int]] | None:
    """Return (filename, size) for every BDMV/STREAM/*.m2ts file, or None."""
    if not iso.has_udf():
        return None
    try:
        children = list(iso.list_children(udf_path=_BDMV_STREAM_UDF_PATH))
    except pycdlibexception.PyCdlibException:
        return None

    files = [
        (name, child.get_data_length())
        for child in children
        if child is not None and child.is_file()  # UDF ".." entries carry a None file_entry
        for name in (child.file_identifier().decode("utf-8", errors="replace"),)
        if name.lower().endswith(_M2TS_SUFFIX)
    ]
    return files or None


def _list_video_ts(iso: "pycdlib.PyCdlib") -> list[tuple[str, int]] | None:
    """Return (filename, size) for every file under VIDEO_TS/, or None."""
    try:
        children = list(iso.list_children(iso_path=_VIDEO_TS_ISO_PATH))
    except pycdlibexception.PyCdlibException:
        return None

    files = [
        (_strip_iso9660_version(child.file_identifier()), child.get_data_length())
        for child in children
        if child.is_file()
    ]
    return files or None


def _strip_iso9660_version(raw_identifier: bytes) -> str:
    """Strip the ISO9660 ";N" version suffix, e.g. b"VTS_01_1.VOB;1" -> "VTS_01_1.VOB"."""
    return raw_identifier.decode("utf-8", errors="replace").split(";", 1)[0]
