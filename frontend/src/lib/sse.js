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
// `onopen` (which fires on both the initial connection and every subsequent
// auto-reconnect) fetches a fresh snapshot via `listJobs()` and hands it to
// `onResync`, so the caller always has a chance to reseed its full job list
// right as the live event stream picks up.

import { listJobs } from './api.js';

/**
 * Open a connection to the job stream and dispatch updates to `handlers`.
 *
 * @param {{onResync?: (jobs: object[]) => void, onUpsert?: (job: object) => void, onDelete?: (jobId: number) => void}} handlers
 * @returns {{close: () => void}} handle to close the underlying EventSource
 */
export function connectJobStream(handlers = {}) {
  const eventSource = new EventSource('/api/jobs/stream');

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
