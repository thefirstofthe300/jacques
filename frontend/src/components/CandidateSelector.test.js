import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import CandidateSelector from './CandidateSelector.svelte';
import { getCandidates, selectMatch } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({
  getCandidates: vi.fn(),
  selectMatch: vi.fn(),
}));

afterEach(cleanup);

function makeJob(overrides = {}) {
  return {
    id: 1,
    disc_type: 'unknown',
    candidates: [],
    ...overrides,
  };
}

const MOVIE_CANDIDATE = {
  tmdb_id: 603,
  title: 'The Matrix',
  year: 1999,
  disc_type: 'movie',
  overview: 'A hacker discovers reality is a simulation.',
};

describe('CandidateSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders job.candidates immediately without a search', () => {
    const job = makeJob({ candidates: [MOVIE_CANDIDATE] });

    render(CandidateSelector, { job });

    expect(screen.getByText('The Matrix')).toBeTruthy();
    expect(getCandidates).not.toHaveBeenCalled();
  });

  it('searches movie candidates and renders results with a Select button', async () => {
    getCandidates.mockResolvedValue([MOVIE_CANDIDATE]);
    const job = makeJob();

    render(CandidateSelector, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Movie/ }));

    expect(getCandidates).toHaveBeenCalledWith(1, 'movie');
    await waitFor(() => expect(screen.getByText('The Matrix')).toBeTruthy());
    expect(screen.getByText(/1999/)).toBeTruthy();
    expect(screen.getByText(/simulation/)).toBeTruthy();
  });

  it('searches tv_show candidates on the TV Show button', async () => {
    getCandidates.mockResolvedValue([]);
    const job = makeJob();

    render(CandidateSelector, { job });

    await fireEvent.click(screen.getByRole('button', { name: /TV Show/ }));

    expect(getCandidates).toHaveBeenCalledWith(1, 'tv_show');
  });

  it('shows a "no matches" message when a search returns empty', async () => {
    getCandidates.mockResolvedValue([]);
    const job = makeJob();

    render(CandidateSelector, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Movie/ }));

    await waitFor(() => expect(screen.getByText(/No matches found/)).toBeTruthy());
  });

  it('calls selectMatch with the candidate tmdb_id and disc_type on Select', async () => {
    getCandidates.mockResolvedValue([MOVIE_CANDIDATE]);
    const job = makeJob();

    render(CandidateSelector, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Movie/ }));
    await waitFor(() => expect(screen.getByText('The Matrix')).toBeTruthy());

    await fireEvent.click(screen.getByRole('button', { name: /^Select$/ }));

    expect(selectMatch).toHaveBeenCalledWith(1, 603, 'movie');
  });

  it('submits a manual TMDB ID override with the selected type', async () => {
    const job = makeJob({ disc_type: 'tv_show' });

    render(CandidateSelector, { job });

    const idInput = screen.getByPlaceholderText('TMDB ID');
    await fireEvent.input(idInput, { target: { value: '1399' } });

    await fireEvent.click(screen.getByRole('button', { name: /Use this ID/ }));

    // disc_type of 'tv_show' should default the manual type selector to tv_show.
    expect(selectMatch).toHaveBeenCalledWith(1, 1399, 'tv_show');
  });

  it('does not submit the manual override when no id is entered', async () => {
    const job = makeJob();

    render(CandidateSelector, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Use this ID/ }));

    expect(selectMatch).not.toHaveBeenCalled();
  });
});
