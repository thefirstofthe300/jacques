import asyncio
import logging

log = logging.getLogger(__name__)

_DEFAULT_QUEUE_MAXSIZE = 100
_DEFAULT_MAX_SUBSCRIBERS = 50


class Broadcaster:
    """Fan-out publisher for job-status SSE events.

    Bounded on two axes to protect against a slow/stalled consumer (e.g. a
    backgrounded browser tab or a buggy script) exhausting server memory or
    file descriptors on this local-network, no-auth home server:

    - Each subscriber's queue is capped at `queue_maxsize`. Once full, the
      oldest queued event is dropped to make room for the newest one — this
      is a live-status stream where only the latest state matters, not a
      strict at-least-once event log (the frontend already treats state as
      eventually consistent, resyncing via `listJobs()` on every SSE
      connect/reconnect). Dropping the oldest event keeps a slow consumer
      caught up to the most recent job states rather than stuck further and
      further behind stale ones.
    - The total subscriber count is capped at `max_subscribers`. Once
      reached, `subscribe()` raises `SubscriberLimitReached` instead of
      growing the subscriber set without bound.
    """

    class SubscriberLimitReached(RuntimeError):
        """Raised by `subscribe()` when `max_subscribers` are already active."""

    def __init__(
        self,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
        max_subscribers: int = _DEFAULT_MAX_SUBSCRIBERS,
    ) -> None:
        self._queue_maxsize = queue_maxsize
        self._max_subscribers = max_subscribers
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        if len(self._subscribers) >= self._max_subscribers:
            raise Broadcaster.SubscriberLimitReached(
                f"maximum of {self._max_subscribers} subscribers already active"
            )
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow/stalled consumer: evict the oldest queued event to
                # make room for the newest one rather than blocking or
                # growing the queue further. Both operations are
                # non-blocking `*_nowait` calls with no `await` between them,
                # so nothing else can interleave on this queue in between.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning(
                        "Broadcaster: subscriber queue still full after evicting "
                        "oldest event; dropping newest event instead"
                    )
