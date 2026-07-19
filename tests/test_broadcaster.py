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


@pytest.mark.asyncio
async def test_publish_bounds_queue_size_and_keeps_newest_events():
    """A subscriber that never reads must not accumulate unbounded events —
    the queue is capped, and once full, publishing evicts the oldest queued
    event so the retained events are always the most recent ones."""
    broadcaster = Broadcaster(queue_maxsize=5)
    queue = broadcaster.subscribe()

    for i in range(20):
        broadcaster.publish({"type": "job_upserted", "job_id": i})

    assert queue.qsize() == 5

    retained = [queue.get_nowait()["job_id"] for _ in range(5)]
    assert retained == [15, 16, 17, 18, 19]


@pytest.mark.asyncio
async def test_publish_bounding_is_per_subscriber():
    """A slow subscriber's full queue must not affect delivery to other,
    healthy subscribers."""
    broadcaster = Broadcaster(queue_maxsize=2)
    slow_queue = broadcaster.subscribe()
    healthy_queue = broadcaster.subscribe()

    for i in range(5):
        broadcaster.publish({"type": "job_upserted", "job_id": i})
        healthy_queue.get_nowait()

    assert slow_queue.qsize() == 2
    assert [slow_queue.get_nowait()["job_id"] for _ in range(2)] == [3, 4]


def test_subscribe_raises_once_max_subscribers_reached():
    broadcaster = Broadcaster(max_subscribers=3)
    broadcaster.subscribe()
    broadcaster.subscribe()
    broadcaster.subscribe()

    with pytest.raises(Broadcaster.SubscriberLimitReached):
        broadcaster.subscribe()


def test_subscribe_succeeds_again_after_unsubscribe_frees_a_slot():
    broadcaster = Broadcaster(max_subscribers=3)
    queue_a = broadcaster.subscribe()
    broadcaster.subscribe()
    broadcaster.subscribe()

    with pytest.raises(Broadcaster.SubscriberLimitReached):
        broadcaster.subscribe()

    broadcaster.unsubscribe(queue_a)

    # A slot is now free; this must succeed without raising.
    new_queue = broadcaster.subscribe()
    assert new_queue is not None
    assert len(broadcaster._subscribers) == 3
