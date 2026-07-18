import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import TitleSelector from './TitleSelector.svelte';
import { keepTitle } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({
  keepTitle: vi.fn(),
}));

afterEach(cleanup);

function makeJob(overrides = {}) {
  return {
    id: 1,
    titles: [
      { id: 10, name: 'Title 1', duration_seconds: 3725 },
      { id: 11, name: null, duration_seconds: 61 },
    ],
    ...overrides,
  };
}

describe('TitleSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders each title with a formatted H:MM:SS duration', () => {
    const job = makeJob();

    render(TitleSelector, { job });

    expect(screen.getByText('Title 1')).toBeTruthy();
    expect(screen.getByText(/1:02:05/)).toBeTruthy();

    // No name falls back to "Title <id>", matching the old template.
    expect(screen.getByText('Title 11')).toBeTruthy();
    expect(screen.getByText(/0:01:01/)).toBeTruthy();
  });

  it('calls keepTitle with the job and title id after confirmation', async () => {
    const job = makeJob();

    render(TitleSelector, { job });

    const buttons = screen.getAllByRole('button', { name: /Keep this one/ });
    await fireEvent.click(buttons[0]);

    expect(window.confirm).toHaveBeenCalled();
    expect(keepTitle).toHaveBeenCalledWith(1, 10);
  });

  it('does not call keepTitle when confirmation is declined', async () => {
    window.confirm.mockReturnValue(false);
    const job = makeJob();

    render(TitleSelector, { job });

    const buttons = screen.getAllByRole('button', { name: /Keep this one/ });
    await fireEvent.click(buttons[1]);

    expect(keepTitle).not.toHaveBeenCalled();
  });

  it('renders nothing when there are no titles', () => {
    const job = makeJob({ titles: [] });

    render(TitleSelector, { job });

    expect(screen.queryByRole('button', { name: /Keep this one/ })).toBeNull();
  });

  it('shows an error message when keepTitle rejects', async () => {
    keepTitle.mockRejectedValue(new Error('POST /1/keep-title/10 failed (409): Job is not awaiting title selection'));
    const job = makeJob();

    render(TitleSelector, { job });

    const buttons = screen.getAllByRole('button', { name: /Keep this one/ });
    await fireEvent.click(buttons[0]);

    await waitFor(() =>
      expect(screen.getByText(/Job is not awaiting title selection/)).toBeTruthy(),
    );
  });
});
