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
// native EventSource behavior — no custom retry logic here.

/**
 * Open a connection to the job stream and dispatch updates to `handlers`.
 *
 * @param {{onUpsert?: (job: object) => void, onDelete?: (jobId: number) => void}} handlers
 * @returns {{close: () => void}} handle to close the underlying EventSource
 */
export function connectJobStream(handlers = {}) {
  const eventSource = new EventSource('/api/jobs/stream');

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
