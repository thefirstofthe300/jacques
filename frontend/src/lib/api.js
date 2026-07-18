// REST client for the jacques backend (jacques/api/routes/jobs.py).
//
// Every function throws a descriptive Error on a non-ok response (using the
// FastAPI HTTPException `detail` field when present) and resolves with parsed
// JSON for endpoints that return a body. Endpoints that respond 202/204 with
// no meaningful payload resolve with `undefined`.

const BASE_URL = '/api/jobs';

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, options);

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // Response body wasn't JSON (or was empty); fall back to statusText.
    }
    throw new Error(`${options.method ?? 'GET'} ${path} failed (${response.status}): ${detail}`);
  }

  if (response.status === 204 || response.status === 202) {
    return undefined;
  }

  return response.json();
}

export function listJobs() {
  return request('');
}

export function getJob(id) {
  return request(`/${id}`);
}

export function rerunStage(id, stage) {
  return request(`/${id}/rerun/${stage}`, { method: 'POST' });
}

export function selectMatch(id, tmdbId, discType) {
  const query = discType ? `?disc_type=${encodeURIComponent(discType)}` : '';
  return request(`/${id}/select/${tmdbId}${query}`, { method: 'POST' });
}

export function assignEpisodes(id, assignments) {
  return request(`/${id}/assign-episodes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(assignments),
  });
}

export function keepTitle(id, titleId) {
  return request(`/${id}/keep-title/${titleId}`, { method: 'POST' });
}

export function deleteJob(id) {
  return request(`/${id}`, { method: 'DELETE' });
}

export function rerip(id) {
  return request(`/${id}/rerip`, { method: 'POST' });
}

export function getCandidates(id, discType) {
  const query = discType ? `?disc_type=${encodeURIComponent(discType)}` : '';
  return request(`/${id}/candidates${query}`);
}
