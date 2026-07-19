import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { listJobs } from './api.js';
import { connectJobStream } from './sse.js';

vi.mock('./api.js', () => ({
  listJobs: vi.fn(),
}));

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.close = vi.fn();
    FakeEventSource.instances.push(this);
  }

  addEventListener(name, callback) {
    this.listeners[name] = this.listeners[name] || [];
    this.listeners[name].push(callback);
  }

  dispatch(name, data) {
    for (const callback of this.listeners[name] || []) {
      callback({ data: JSON.stringify(data) });
    }
  }
}
FakeEventSource.instances = [];

describe('connectJobStream', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    listJobs.mockReset();
    // connectJobStream always fetches an initial snapshot immediately, so
    // every test needs a working default even if it isn't asserting on it.
    listJobs.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches a fresh snapshot via listJobs immediately, without waiting for the connection to open', async () => {
    // The initial load must not depend on EventSource's `open` event firing —
    // some browsers won't fire it promptly (or possibly at all) on a stream
    // that hasn't sent any bytes yet, which happens whenever nothing is
    // currently active server-side.
    const jobs = [{ id: 1, status: 'RIPPING' }];
    listJobs.mockResolvedValue(jobs);
    const onResync = vi.fn();
    connectJobStream({ onResync });

    await vi.waitFor(() => expect(onResync).toHaveBeenCalledWith(jobs));
  });

  it('resyncs again when the connection opens, and again on every reconnect', async () => {
    const onResync = vi.fn();
    listJobs.mockResolvedValueOnce([{ id: 1, status: 'DETECTED' }]);
    connectJobStream({ onResync });
    await vi.waitFor(() => expect(onResync).toHaveBeenCalledTimes(1));

    listJobs.mockResolvedValueOnce([{ id: 1, status: 'RIPPING' }]);
    FakeEventSource.instances[0].dispatch('open');
    await vi.waitFor(() => expect(onResync).toHaveBeenCalledTimes(2));

    listJobs.mockResolvedValueOnce([{ id: 1, status: 'TRANSCODING' }]);
    FakeEventSource.instances[0].dispatch('open');
    await vi.waitFor(() => expect(onResync).toHaveBeenCalledTimes(3));

    expect(onResync).toHaveBeenNthCalledWith(1, [{ id: 1, status: 'DETECTED' }]);
    expect(onResync).toHaveBeenNthCalledWith(2, [{ id: 1, status: 'RIPPING' }]);
    expect(onResync).toHaveBeenNthCalledWith(3, [{ id: 1, status: 'TRANSCODING' }]);
  });

  it('opens an EventSource against /api/jobs/stream', () => {
    connectJobStream({});

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe('/api/jobs/stream');
  });

  it('dispatches job_upserted payloads to onUpsert', () => {
    const onUpsert = vi.fn();
    const onDelete = vi.fn();
    connectJobStream({ onUpsert, onDelete });

    const job = { id: 1, status: 'RIPPING' };
    FakeEventSource.instances[0].dispatch('job-update', { type: 'job_upserted', job });

    expect(onUpsert).toHaveBeenCalledWith(job);
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('dispatches job_deleted payloads to onDelete', () => {
    const onUpsert = vi.fn();
    const onDelete = vi.fn();
    connectJobStream({ onUpsert, onDelete });

    FakeEventSource.instances[0].dispatch('job-update', { type: 'job_deleted', job_id: 42 });

    expect(onDelete).toHaveBeenCalledWith(42);
    expect(onUpsert).not.toHaveBeenCalled();
  });

  it('ignores unknown event types without throwing', () => {
    const onUpsert = vi.fn();
    const onDelete = vi.fn();
    connectJobStream({ onUpsert, onDelete });

    expect(() =>
      FakeEventSource.instances[0].dispatch('job-update', { type: 'something_else' }),
    ).not.toThrow();
    expect(onUpsert).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('close() on the returned handle closes the underlying EventSource', () => {
    const handle = connectJobStream({});

    handle.close();

    expect(FakeEventSource.instances[0].close).toHaveBeenCalledOnce();
  });
});
