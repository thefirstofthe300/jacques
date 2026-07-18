import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import EpisodeAssignmentForm from './EpisodeAssignmentForm.svelte';
import { assignEpisodes } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({
  assignEpisodes: vi.fn(),
}));

afterEach(cleanup);

function makeJob(overrides = {}) {
  return {
    id: 1,
    titles: [
      { id: 10, name: 'Episode A', duration_seconds: 1500 },
      { id: 11, name: null, duration_seconds: 1600 },
    ],
    ...overrides,
  };
}

describe('EpisodeAssignmentForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a season/episode/name input row per title with correct defaults', () => {
    const job = makeJob();

    render(EpisodeAssignmentForm, { job });

    expect(screen.getByText('Episode A')).toBeTruthy();
    expect(screen.getByText('Title 11')).toBeTruthy();

    const seasonInputs = screen.getAllByPlaceholderText('Season');
    const episodeInputs = screen.getAllByPlaceholderText('Episode');
    expect(seasonInputs).toHaveLength(2);
    expect(episodeInputs).toHaveLength(2);

    // Season defaults to 1 for every title.
    expect(seasonInputs[0].value).toBe('1');
    expect(seasonInputs[1].value).toBe('1');

    // Episode defaults to the 1-based index within job.titles.
    expect(episodeInputs[0].value).toBe('1');
    expect(episodeInputs[1].value).toBe('2');
  });

  it('gathers all rows into assignEpisodes on Save & Continue', async () => {
    const job = makeJob();

    render(EpisodeAssignmentForm, { job });

    const seasonInputs = screen.getAllByPlaceholderText('Season');
    const episodeInputs = screen.getAllByPlaceholderText('Episode');
    const nameInputs = screen.getAllByPlaceholderText('Episode name');

    await fireEvent.input(seasonInputs[0], { target: { value: '2' } });
    await fireEvent.input(episodeInputs[0], { target: { value: '5' } });
    await fireEvent.input(nameInputs[0], { target: { value: 'Pilot' } });

    await fireEvent.click(screen.getByRole('button', { name: /Save & Continue/ }));

    expect(assignEpisodes).toHaveBeenCalledWith(1, [
      { title_id: 10, season: 2, episode: 5, name: 'Pilot' },
      { title_id: 11, season: 1, episode: 2, name: '' },
    ]);
  });
});
