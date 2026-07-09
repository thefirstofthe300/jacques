# Feature 01: Disc Detection

**Status:** Complete

## Summary

Watch Linux udev for optical drive events. When a disc with readable media is inserted, create a Job record and enqueue it for processing.

## Implementation

- `services/detector.py` — `DiscDetector` class
- Uses `pyudev.Monitor` polling with a 1-second timeout in `asyncio.to_thread`
- Filters for `subsystem=block`, `action=change`, `ID_TYPE=cd`, `ID_CDROM_MEDIA=1`
- Calls `on_disc_inserted(drive_path, disc_label)` coroutine on detection
- Supports multiple concurrent drives (each triggers its own job)

## Udev Properties Used

| Property | Meaning |
|---|---|
| `ID_TYPE=cd` | Device is an optical drive |
| `ID_CDROM_MEDIA=1` | Disc with readable media is present |
| `ID_FS_LABEL` | Disc volume label (used as initial disc label) |
| `device_node` | Path like `/dev/sr0` |

## Edge Cases

- Disc ejected before processing starts: job stays in DETECTED state, ripping will fail cleanly
- No label: falls back to drive path as identifier
- Multiple discs inserted simultaneously: each gets its own job and runs concurrently
