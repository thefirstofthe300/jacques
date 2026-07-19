// Reactive job store. Holds jobs in a Map keyed by id, and exposes a derived
// list sorted by created_at descending (matching the backend's
// `ORDER BY created_at DESC` in jacques/api/routes/jobs.py's list_jobs).

import { writable, derived } from 'svelte/store';

export const jobs = writable(new Map());

export const jobList = derived(jobs, ($jobs) =>
  Array.from($jobs.values()).sort(
    (a, b) => new Date(b.created_at) - new Date(a.created_at),
  ),
);

export function upsertJob(job) {
  jobs.update((current) => {
    const next = new Map(current);
    next.set(job.id, job);
    return next;
  });
}

export function removeJob(jobId) {
  jobs.update((current) => {
    const next = new Map(current);
    next.delete(jobId);
    return next;
  });
}

export function setJobs(jobArray) {
  jobs.set(new Map(jobArray.map((job) => [job.id, job])));
}
