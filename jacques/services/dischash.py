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
"""

import hashlib
import logging
import struct

import pycdlib
from pycdlib import pycdlibexception

log = logging.getLogger(__name__)

_BDMV_STREAM_UDF_PATH = "/BDMV/STREAM"
_VIDEO_TS_ISO_PATH = "/VIDEO_TS"
_M2TS_SUFFIX = ".m2ts"


def compute_content_hash(drive_path: str) -> str | None:
    """Compute TheDiscDB ContentHash for the disc at `drive_path`.

    Opens the raw block device (or image file) at `drive_path` with pycdlib
    and looks for BDMV/STREAM (Blu-ray, via the UDF filesystem view) or
    VIDEO_TS (DVD, via the ISO9660 view) content, trying Blu-ray first since
    Blu-ray discs are mastered as "UDF Bridge" (both UDF and ISO9660) while
    DVDs are pure ISO9660. Disc format is auto-detected this way rather than
    taken as a parameter, since Jacques's own `DiscType` (movie/tv_show/
    unknown) encodes content type, not physical disc format.

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
    iso.open(drive_path)
    try:
        files = _list_bdmv_stream(iso) or _list_video_ts(iso)
        if not files:
            log.warning(
                "No BDMV/STREAM or VIDEO_TS content found on %s; cannot compute content hash",
                drive_path,
            )
            return None

        digest = hashlib.md5()
        for _name, size in sorted(files, key=lambda entry: entry[0]):
            digest.update(struct.pack("<Q", size))
        return digest.hexdigest()
    finally:
        iso.close()


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
