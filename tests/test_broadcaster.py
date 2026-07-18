import asyncio

import pytest

from jacques.services.broadcaster import Broadcaster


@pytest.mark.asyncio
async def test_subscribe_receives_published_event():
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()

    broadcaster.publish({"type": "job_upserted", "job_id": 1, "status": "ripping"})

    event = queue.get_nowait()
    assert event == {"type": "job_upserted", "job_id": 1, "status": "ripping"}


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive_same_event():
    broadcaster = Broadcaster()
    queue_a = broadcaster.subscribe()
    queue_b = broadcaster.subscribe()

    broadcaster.publish({"type": "job_upserted", "job_id": 2, "status": "complete"})

    assert queue_a.get_nowait() == {"type": "job_upserted", "job_id": 2, "status": "complete"}
    assert queue_b.get_nowait() == {"type": "job_upserted", "job_id": 2, "status": "complete"}


@pytest.mark.asyncio
async def test_unsubscribe_stops_further_delivery():
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()
    broadcaster.unsubscribe(queue)

    broadcaster.publish({"type": "job_upserted", "job_id": 3, "status": "failed"})

    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


def test_unsubscribe_unknown_queue_does_not_raise():
    broadcaster = Broadcaster()
    stray_queue: asyncio.Queue = asyncio.Queue()

    broadcaster.unsubscribe(stray_queue)
