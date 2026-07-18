import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { jobs, jobList, upsertJob, removeJob, setJobs } from './store.js';

describe('job store', () => {
  beforeEach(() => {
    setJobs([]);
  });

  it('upsertJob adds a new job to the map', () => {
    upsertJob({ id: 1, created_at: '2026-01-01T00:00:00Z' });

    expect(get(jobs).get(1)).toEqual({ id: 1, created_at: '2026-01-01T00:00:00Z' });
  });

  it('upsertJob replaces an existing job with the same id', () => {
    upsertJob({ id: 1, created_at: '2026-01-01T00:00:00Z', status: 'RIPPING' });
    upsertJob({ id: 1, created_at: '2026-01-01T00:00:00Z', status: 'COMPLETE' });

    expect(get(jobs).size).toBe(1);
    expect(get(jobs).get(1).status).toBe('COMPLETE');
  });

  it('removeJob deletes a job from the map', () => {
    upsertJob({ id: 1, created_at: '2026-01-01T00:00:00Z' });
    upsertJob({ id: 2, created_at: '2026-01-02T00:00:00Z' });

    removeJob(1);

    const current = get(jobs);
    expect(current.has(1)).toBe(false);
    expect(current.has(2)).toBe(true);
  });

  it('setJobs bulk-replaces the map contents', () => {
    upsertJob({ id: 1, created_at: '2026-01-01T00:00:00Z' });

    setJobs([
      { id: 2, created_at: '2026-01-02T00:00:00Z' },
      { id: 3, created_at: '2026-01-03T00:00:00Z' },
    ]);

    const current = get(jobs);
    expect(current.has(1)).toBe(false);
    expect(current.size).toBe(2);
  });

  it('jobList derives an array sorted by created_at descending', () => {
    setJobs([
      { id: 1, created_at: '2026-01-01T00:00:00Z' },
      { id: 2, created_at: '2026-01-03T00:00:00Z' },
      { id: 3, created_at: '2026-01-02T00:00:00Z' },
    ]);

    expect(get(jobList).map((job) => job.id)).toEqual([2, 3, 1]);
  });
});
