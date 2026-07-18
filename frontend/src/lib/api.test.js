import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  listJobs,
  getJob,
  rerunStage,
  selectMatch,
  assignEpisodes,
  keepTitle,
  deleteJob,
  rerip,
  getCandidates,
} from './api.js';

function mockResponse({ ok = true, status = 200, json } = {}) {
  return {
    ok,
    status,
    statusText: 'error',
    json: json ? () => Promise.resolve(json) : () => Promise.reject(new Error('no body')),
  };
}

describe('api client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  it('listJobs calls GET /api/jobs and returns parsed json', async () => {
    const jobs = [{ id: 1 }, { id: 2 }];
    fetch.mockResolvedValueOnce(mockResponse({ json: jobs }));

    const result = await listJobs();

    expect(fetch).toHaveBeenCalledWith('/api/jobs', {});
    expect(result).toEqual(jobs);
  });

  it('getJob calls GET /api/jobs/{id}', async () => {
    const job = { id: 42 };
    fetch.mockResolvedValueOnce(mockResponse({ json: job }));

    const result = await getJob(42);

    expect(fetch).toHaveBeenCalledWith('/api/jobs/42', {});
    expect(result).toEqual(job);
  });

  it('rerunStage POSTs to /api/jobs/{id}/rerun/{stage} and returns undefined for 202', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 202 }));

    const result = await rerunStage(1, 'ripping');

    expect(fetch).toHaveBeenCalledWith('/api/jobs/1/rerun/ripping', { method: 'POST' });
    expect(result).toBeUndefined();
  });

  it('selectMatch POSTs to /api/jobs/{id}/select/{tmdbId} with disc_type query param', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 202 }));

    await selectMatch(1, 550, 'movie');

    expect(fetch).toHaveBeenCalledWith('/api/jobs/1/select/550?disc_type=movie', { method: 'POST' });
  });

  it('selectMatch omits the query string when disc_type is not provided', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 202 }));

    await selectMatch(1, 550);

    expect(fetch).toHaveBeenCalledWith('/api/jobs/1/select/550', { method: 'POST' });
  });

  it('assignEpisodes POSTs a JSON body to /api/jobs/{id}/assign-episodes', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 202 }));
    const assignments = [{ title_id: 1, season: 1, episode: 1, name: 'Pilot' }];

    await assignEpisodes(7, assignments);

    expect(fetch).toHaveBeenCalledWith('/api/jobs/7/assign-episodes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(assignments),
    });
  });

  it('keepTitle POSTs to /api/jobs/{id}/keep-title/{titleId}', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 202 }));

    await keepTitle(3, 9);

    expect(fetch).toHaveBeenCalledWith('/api/jobs/3/keep-title/9', { method: 'POST' });
  });

  it('deleteJob issues a DELETE and returns undefined for 204', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 204 }));

    const result = await deleteJob(5);

    expect(fetch).toHaveBeenCalledWith('/api/jobs/5', { method: 'DELETE' });
    expect(result).toBeUndefined();
  });

  it('rerip POSTs to /api/jobs/{id}/rerip', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ status: 202 }));

    await rerip(11);

    expect(fetch).toHaveBeenCalledWith('/api/jobs/11/rerip', { method: 'POST' });
  });

  it('getCandidates calls GET /api/jobs/{id}/candidates with disc_type query param', async () => {
    const candidates = [{ tmdb_id: 1, title: 'Foo', year: 2000, disc_type: 'movie', overview: '...' }];
    fetch.mockResolvedValueOnce(mockResponse({ json: candidates }));

    const result = await getCandidates(1, 'movie');

    expect(fetch).toHaveBeenCalledWith('/api/jobs/1/candidates?disc_type=movie', {});
    expect(result).toEqual(candidates);
  });

  it('throws a descriptive error including the detail field on a non-ok response', async () => {
    fetch.mockResolvedValueOnce(
      mockResponse({ ok: false, status: 404, json: { detail: 'Job not found' } }),
    );

    await expect(getJob(999)).rejects.toThrow(/Job not found/);
  });

  it('falls back to statusText when the error response has no JSON body', async () => {
    fetch.mockResolvedValueOnce(mockResponse({ ok: false, status: 500 }));

    await expect(getJob(1)).rejects.toThrow(/error/);
  });
});
