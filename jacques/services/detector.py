import asyncio
import logging
from collections.abc import Awaitable, Callable

import pyudev

log = logging.getLogger(__name__)

OnDiscInserted = Callable[[str, str | None], Awaitable[None]]


class DiscDetector:
    """Monitors udev for optical disc insertion events.

    Calls on_disc_inserted(drive_path, disc_label) for each detected disc.
    Runs until the asyncio task is cancelled.
    """

    def __init__(self, on_disc_inserted: OnDiscInserted) -> None:
        self._on_disc_inserted = on_disc_inserted

    async def run(self) -> None:
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="block")
        monitor.start()

        log.info("Disc detector running — watching for optical disc insertion")

        # Enumerate discs already present so a daemon restart doesn't miss them.
        for device in context.list_devices(subsystem="block"):
            if device.get("ID_CDROM_MEDIA") != "1":
                continue
            drive_path: str = device.device_node
            disc_label: str | None = device.get("ID_FS_LABEL") or None
            log.info("Disc already present: %s (label=%r)", drive_path, disc_label)
            await self._on_disc_inserted(drive_path, disc_label)

        while True:
            device: pyudev.Device | None = await asyncio.get_running_loop().run_in_executor(
                None, lambda: monitor.poll(timeout=1.0)
            )

            if device is None:
                continue

            if device.action != "change":
                continue

            if device.get("ID_TYPE") != "cd":
                continue

            if device.get("ID_CDROM_MEDIA") != "1":
                continue

            drive_path: str = device.device_node
            disc_label: str | None = device.get("ID_FS_LABEL") or None

            log.info("Disc inserted: %s (label=%r)", drive_path, disc_label)
            await self._on_disc_inserted(drive_path, disc_label)
