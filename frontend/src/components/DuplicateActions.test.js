import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import DuplicateActions from './DuplicateActions.svelte';
import { rerip, deleteJob } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({
  rerip: vi.fn(),
  deleteJob: vi.fn(),
}));

afterEach(cleanup);

function makeJob(overrides = {}) {
  return {
    id: 1,
    disc_label: null,
    ...overrides,
  };
}

describe('DuplicateActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('shows the info message without a disc label when none is set', () => {
    const job = makeJob();

    render(DuplicateActions, { job });

    expect(screen.getByText(/previously ripped/)).toBeTruthy();
    expect(screen.queryByText(/^Disc:/)).toBeNull();
  });

  it('shows the disc_label when present', () => {
    const job = makeJob({ disc_label: 'MATRIX_DISC' });

    render(DuplicateActions, { job });

    expect(screen.getByText('Disc: MATRIX_DISC')).toBeTruthy();
  });

  it('calls rerip after confirmation when Re-rip is clicked', async () => {
    const job = makeJob();

    render(DuplicateActions, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Re-rip/ }));

    expect(window.confirm).toHaveBeenCalled();
    expect(rerip).toHaveBeenCalledWith(1);
  });

  it('does not call rerip when the confirmation is declined', async () => {
    window.confirm.mockReturnValue(false);
    const job = makeJob();

    render(DuplicateActions, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Re-rip/ }));

    expect(rerip).not.toHaveBeenCalled();
  });

  it('calls deleteJob after confirmation when Dismiss is clicked', async () => {
    const job = makeJob();

    render(DuplicateActions, { job });

    await fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }));

    expect(window.confirm).toHaveBeenCalled();
    expect(deleteJob).toHaveBeenCalledWith(1);
  });
});
