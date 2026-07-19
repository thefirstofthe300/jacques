// SSE client for the jacques live-updates endpoint (GET /api/jobs/stream).
//
// The server emits a single event name, `job-update`, for both upserts and
// deletes, discriminated by the `type` field in the JSON payload:
//   event: job-update
//   data: {"type": "job_upserted", "job": {...}}
// or
//   event: job-update
//   data: {"type": "job_deleted", "job_id": 123}
//
// Reconnection on a dropped connection is left entirely to the browser's
// native EventSource behavior — no custom retry logic here. However, a
// reconnect (or the very first connect) can miss events published in the gap
// between the server registering the subscription and this client actually
// being ready to receive them, and the backend's Broadcaster has no
// replay/backlog for a queue that wasn't subscribed yet. To close that gap,
// every connect/reconnect fetches a fresh snapshot via `listJobs()` and hands
// it to `onResync`.
//
// The initial fetch happens immediately when this function is called, NOT
// gated behind EventSource's `open` event — different browsers vary in when
// (or whether) they consider a streaming response "open" if the server
// hasn't sent any bytes yet, which it may not for a long time if nothing is
// currently active. Gating the first load on `open` alone previously caused
// the job list to never populate in that case. `open` still triggers a
// resync on every reconnect after the first, since a dropped connection can
// resume well after the initial load.

import { listJobs } from './api.js';

/**
 * Open a connection to the job stream and dispatch updates to `handlers`.
 *
 * @param {{onResync?: (jobs: object[]) => void, onUpsert?: (job: object) => void, onDelete?: (jobId: number) => void}} handlers
 * @returns {{close: () => void}} handle to close the underlying EventSource
 */
export function connectJobStream(handlers = {}) {
  const eventSource = new EventSource('/api/jobs/stream');

  listJobs().then((jobs) => handlers.onResync?.(jobs));

  eventSource.addEventListener('open', () => {
    listJobs().then((jobs) => handlers.onResync?.(jobs));
  });

  eventSource.addEventListener('job-update', (event) => {
    const payload = JSON.parse(event.data);

    switch (payload.type) {
      case 'job_upserted':
        handlers.onUpsert?.(payload.job);
        break;
      case 'job_deleted':
        handlers.onDelete?.(payload.job_id);
        break;
      default:
        break;
    }
  });

  return {
    close: () => eventSource.close(),
  };
}
