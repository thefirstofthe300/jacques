"""Unit tests for DiscDetector.

pyudev requires real hardware, so the entire pyudev module is mocked out.
The tests verify that the callback receives (drive_path, disc_label, disc_uuid)
with the correct values under each scenario.

Strategy for stopping the event loop
--------------------------------------
DiscDetector.run() is an infinite loop that calls monitor.poll() inside
run_in_executor().  Raising CancelledError *inside* the executor thread does
not cancel the asyncio task cleanly — it surfaces as a bare exception from the
thread pool.  Instead, each test:

  1. Sets up mock poll() responses for the disc events it cares about, then
     returns None (no device) indefinitely after that.
  2. Wraps detector.run() in an asyncio.Task, then cancels it after an
     asyncio.sleep(0) yields control, allowing the loop to process all queued
     events before the task is cancelled.
  3. Asserts on the captured callback calls.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from jacques.services.detector import DiscDetector


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_device(
    *,
    device_node: str = "/dev/sr0",
    id_cdrom_media: str | None = "1",
    id_type: str | None = "cd",
    action: str = "change",
    id_fs_label: str | None = None,
    id_fs_uuid: str | None = None,
) -> MagicMock:
    """Build a fake pyudev Device with the given property values."""
    device = MagicMock()
    device.device_node = device_node
    device.action = action

    props: dict[str, str] = {}
    if id_cdrom_media is not None:
        props["ID_CDROM_MEDIA"] = id_cdrom_media
    if id_type is not None:
        props["ID_TYPE"] = id_type
    if id_fs_label is not None:
        props["ID_FS_LABEL"] = id_fs_label
    if id_fs_uuid is not None:
        props["ID_FS_UUID"] = id_fs_uuid

    device.get = lambda key, default=None: props.get(key, default)
    return device


def _make_monitor(poll_devices: list) -> MagicMock:
    """Build a fake pyudev Monitor.

    poll() returns each device in poll_devices in order, then returns None
    forever (so the loop idles and can be cancelled cleanly from outside).
    """
    # Convert the finite list into an iterator; once exhausted, return None.
    device_iter = iter(poll_devices)

    def _poll(timeout=1.0):
        return next(device_iter, None)

    monitor = MagicMock()
    monitor.poll = MagicMock(side_effect=_poll)
    return monitor


async def _run_until_drained(detector: DiscDetector) -> None:
    """Run detector.run() as a Task, give it one event-loop tick to process
    all queued mock poll() results, then cancel it cleanly."""
    task = asyncio.create_task(detector.run())
    # Yield control so the task processes the queued devices.
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── udev event-loop tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_receives_label_and_uuid_when_both_present():
    """When a disc has both ID_FS_LABEL and ID_FS_UUID the callback gets both."""
    device = _make_device(id_fs_label="THE_MATRIX", id_fs_uuid="ABCD-1234")
    monitor = _make_monitor([device])
    context = MagicMock()
    context.list_devices.return_value = []

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert len(captured) == 1
    assert captured[0] == ("/dev/sr0", "THE_MATRIX", "ABCD-1234")


@pytest.mark.asyncio
async def test_callback_receives_none_uuid_when_absent():
    """When ID_FS_UUID is absent the callback receives disc_uuid=None."""
    device = _make_device(id_fs_label="SOME_DISC", id_fs_uuid=None)
    monitor = _make_monitor([device])
    context = MagicMock()
    context.list_devices.return_value = []

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert len(captured) == 1
    drive_path, disc_label, disc_uuid = captured[0]
    assert disc_uuid is None
    assert disc_label == "SOME_DISC"


@pytest.mark.asyncio
async def test_callback_receives_none_label_when_absent():
    """When ID_FS_LABEL is absent the callback receives disc_label=None."""
    device = _make_device(id_fs_label=None, id_fs_uuid="BEEF-CAFE")
    monitor = _make_monitor([device])
    context = MagicMock()
    context.list_devices.return_value = []

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert len(captured) == 1
    drive_path, disc_label, disc_uuid = captured[0]
    assert disc_label is None
    assert disc_uuid == "BEEF-CAFE"


@pytest.mark.asyncio
async def test_non_cd_device_is_ignored():
    """Devices whose ID_TYPE is not 'cd' must not trigger the callback."""
    non_cd = _make_device(id_type="disk", id_fs_label="DATA_DRIVE")
    monitor = _make_monitor([non_cd])
    context = MagicMock()
    context.list_devices.return_value = []

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert captured == []


@pytest.mark.asyncio
async def test_device_without_media_is_ignored():
    """Devices where ID_CDROM_MEDIA != '1' must not trigger the callback."""
    no_media = _make_device(id_cdrom_media="0")
    monitor = _make_monitor([no_media])
    context = MagicMock()
    context.list_devices.return_value = []

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert captured == []


# ── startup enumeration tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_enumeration_passes_uuid():
    """Already-inserted discs enumerated at startup also deliver disc_uuid."""
    already_present = _make_device(
        id_cdrom_media="1",
        id_fs_label="BREAKING_BAD_S1",
        id_fs_uuid="1234-ABCD",
        action="",  # action is irrelevant for enumeration path
    )
    monitor = _make_monitor([])  # no live events
    context = MagicMock()
    context.list_devices.return_value = [already_present]

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert len(captured) == 1
    assert captured[0] == ("/dev/sr0", "BREAKING_BAD_S1", "1234-ABCD")


@pytest.mark.asyncio
async def test_startup_enumeration_uuid_none_when_absent():
    """Startup-enumerated disc without UUID delivers disc_uuid=None."""
    already_present = _make_device(
        id_cdrom_media="1",
        id_fs_label="OLD_DISC",
        id_fs_uuid=None,
        action="",
    )
    monitor = _make_monitor([])
    context = MagicMock()
    context.list_devices.return_value = [already_present]

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert len(captured) == 1
    _, disc_label, disc_uuid = captured[0]
    assert disc_label == "OLD_DISC"
    assert disc_uuid is None


@pytest.mark.asyncio
async def test_startup_enumeration_skips_non_optical_devices():
    """Devices without ID_CDROM_MEDIA=1 are skipped during startup enumeration."""
    non_optical = _make_device(id_cdrom_media=None, id_fs_label="HDD")
    monitor = _make_monitor([])
    context = MagicMock()
    context.list_devices.return_value = [non_optical]

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert captured == []


@pytest.mark.asyncio
async def test_startup_enumerates_multiple_discs():
    """All already-inserted optical discs are enumerated and reported."""
    disc_a = _make_device(
        device_node="/dev/sr0",
        id_fs_label="DISC_A",
        id_fs_uuid="UUID-A",
        action="",
    )
    disc_b = _make_device(
        device_node="/dev/sr1",
        id_fs_label="DISC_B",
        id_fs_uuid=None,
        action="",
    )
    monitor = _make_monitor([])
    context = MagicMock()
    context.list_devices.return_value = [disc_a, disc_b]

    captured: list[tuple] = []

    async def on_inserted(drive_path, disc_label, disc_uuid):
        captured.append((drive_path, disc_label, disc_uuid))

    with (
        patch("jacques.services.detector.pyudev.Context", return_value=context),
        patch("jacques.services.detector.pyudev.Monitor") as mock_monitor_cls,
    ):
        mock_monitor_cls.from_netlink.return_value = monitor
        await _run_until_drained(DiscDetector(on_disc_inserted=on_inserted))

    assert len(captured) == 2
    assert captured[0] == ("/dev/sr0", "DISC_A", "UUID-A")
    assert captured[1] == ("/dev/sr1", "DISC_B", None)
