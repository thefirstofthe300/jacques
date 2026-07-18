import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import JobCard from './JobCard.svelte';
import { rerunStage, deleteJob } from '../lib/api.js';

// Pause-point child components (DuplicateActions, CandidateSelector,
// TitleSelector, EpisodeAssignmentForm) import from this same module, so the
// mock must cover every export they use even though these tests only
// exercise JobCard's own behavior.
vi.mock('../lib/api.js', () => ({
  rerunStage: vi.fn(),
  deleteJob: vi.fn(),
  rerip: vi.fn(),
  getCandidates: vi.fn(),
  selectMatch: vi.fn(),
  keepTitle: vi.fn(),
  assignEpisodes: vi.fn(),
}));

// @testing-library/svelte auto-registers cleanup via global beforeEach/afterEach
// hooks, but only when those hooks are true globals; this project doesn't set
// `test.globals: true`, so register cleanup explicitly to avoid DOM leaking
// between tests in this file.
afterEach(cleanup);

function makeJob(overrides = {}) {
  return {
    id: 1,
    drive_path: '/dev/sr0',
    disc_label: null,
    disc_uuid: null,
    disc_type: 'unknown',
    status: 'detected',
    title: null,
    year: null,
    progress: 0,
    error_message: null,
    display_name: 'Some Disc',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    candidates: [],
    titles: [],
    episode_assignments: {},
    selected_title_id: null,
    ...overrides,
  };
}

describe('JobCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders a ripping job with progress bar and disc-type badge', () => {
    const job = makeJob({
      status: 'ripping',
      disc_type: 'movie',
      progress: 42,
      display_name: 'The Matrix',
      is_active: true,
    });

    const { container } = render(JobCard, { job });

    expect(screen.getByText('The Matrix')).toBeTruthy();
    expect(screen.getByText('/dev/sr0', { exact: false })).toBeTruthy();
    expect(screen.getByText('ripping')).toBeTruthy();
    expect(screen.getByText('Movie')).toBeTruthy();

    const progressBar = container.querySelector('.progress-bar');
    expect(progressBar).toBeTruthy();
    expect(progressBar.style.width).toBe('42%');
  });

  it('shows disc_label alongside display_name when they differ', () => {
    const job = makeJob({ display_name: 'The Matrix', disc_label: 'MATRIX_DISC' });

    render(JobCard, { job });

    expect(screen.getByText('MATRIX_DISC')).toBeTruthy();
  });

  it('renders a failed job with error message and retry buttons that call rerunStage', async () => {
    const job = makeJob({
      status: 'failed',
      error_message: 'HandBrakeCLI exited with code 1',
      is_active: false,
    });

    render(JobCard, { job });

    expect(screen.getByText('failed')).toBeTruthy();
    expect(screen.getByText('HandBrakeCLI exited with code 1')).toBeTruthy();

    const rerunButton = screen.getByRole('button', { name: /Rip/ });
    await fireEvent.click(rerunButton);

    expect(rerunStage).toHaveBeenCalledWith(1, 'ripping');
  });

  it('renders a complete job without a progress bar but with retry buttons', () => {
    const job = makeJob({ status: 'complete', is_active: false });

    const { container } = render(JobCard, { job });

    expect(screen.getByText('complete')).toBeTruthy();
    expect(container.querySelector('.progress-bar')).toBeFalsy();
    expect(screen.getByRole('button', { name: /Organize/ })).toBeTruthy();
  });

  it('shows a delete button for inactive jobs that calls deleteJob after confirmation', async () => {
    const job = makeJob({ status: 'complete', is_active: false });

    render(JobCard, { job });

    const deleteButton = screen.getByRole('button', { name: /Delete/ });
    await fireEvent.click(deleteButton);

    expect(window.confirm).toHaveBeenCalled();
    expect(deleteJob).toHaveBeenCalledWith(1);
  });

  it('does not render a delete button for active jobs', () => {
    const job = makeJob({ status: 'ripping', is_active: true });

    render(JobCard, { job });

    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
  });

  it('shows an error message when rerunStage rejects', async () => {
    rerunStage.mockRejectedValue(new Error('POST /1/rerun/ripping failed (409): Job is still active'));
    const job = makeJob({ status: 'failed', is_active: false });

    render(JobCard, { job });

    const rerunButton = screen.getByRole('button', { name: /Rip/ });
    await fireEvent.click(rerunButton);

    await waitFor(() => expect(screen.getByText(/Job is still active/)).toBeTruthy());
  });

  it('shows an error message when deleteJob rejects', async () => {
    deleteJob.mockRejectedValue(new Error('DELETE /1 failed (404): Job not found'));
    const job = makeJob({ status: 'complete', is_active: false });

    render(JobCard, { job });

    const deleteButton = screen.getByRole('button', { name: /Delete/ });
    await fireEvent.click(deleteButton);

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText(/Job not found/)).toBeTruthy());
  });

  it('renders the DuplicateActions pause-point form for duplicate_detected', () => {
    const job = makeJob({ status: 'duplicate_detected', is_active: true });

    const { container } = render(JobCard, { job });

    expect(container.querySelector('.duplicate-actions')).toBeTruthy();
    expect(screen.getByText(/previously ripped/)).toBeTruthy();
  });

  it('renders the CandidateSelector pause-point form for awaiting_selection', () => {
    const job = makeJob({ status: 'awaiting_selection', is_active: true });

    const { container } = render(JobCard, { job });

    expect(container.querySelector('.candidate-selector')).toBeTruthy();
  });

  it('does not show the progress bar while status is detected', () => {
    const job = makeJob({ status: 'detected', is_active: true });

    const { container } = render(JobCard, { job });

    expect(container.querySelector('.progress-bar')).toBeFalsy();
  });
});
