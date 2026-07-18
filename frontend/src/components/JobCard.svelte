<script>
  // Renders a single job's status/progress/basic actions. Pause-point forms
  // (candidate select, title select, episode assignment, duplicate actions)
  // are separate components that will slot into the placeholder below based
  // on job.status — not implemented here.
  import { rerunStage, deleteJob } from '../lib/api.js';

  let { job } = $props();

  const STATUS_CLASSES = {
    detected: 'bg-secondary',
    identifying: 'bg-info text-dark',
    ripping: 'bg-primary',
    transcoding: 'bg-primary',
    fetching_metadata: 'bg-warning text-dark',
    organizing: 'bg-warning text-dark',
    awaiting_selection: 'bg-warning text-dark',
    ripping_awaiting_selection: 'bg-warning text-dark',
    awaiting_episode_assignment: 'bg-warning text-dark',
    awaiting_title_selection: 'bg-warning text-dark',
    complete: 'bg-success',
    failed: 'bg-danger',
  };

  const RERUN_STAGES = [
    { stage: 'identifying', label: 'From Start', icon: 'bi-skip-backward' },
    { stage: 'fetching_metadata', label: 'Metadata', icon: 'bi-info-circle' },
    { stage: 'ripping', label: 'Rip', icon: 'bi-disc' },
    { stage: 'transcoding', label: 'Transcode', icon: 'bi-file-earmark-play' },
    { stage: 'organizing', label: 'Organize', icon: 'bi-folder-symlink' },
  ];

  const PAUSE_POINT_STATUSES = new Set([
    'duplicate_detected',
    'awaiting_selection',
    'ripping_awaiting_selection',
    'awaiting_title_selection',
    'awaiting_episode_assignment',
  ]);

  let statusClass = $derived(STATUS_CLASSES[job.status] ?? 'bg-secondary');
  let statusLabel = $derived(job.status.replaceAll('_', ' '));
  let showProgress = $derived(job.is_active && job.status !== 'detected');
  let showRetry = $derived(job.status === 'failed' || job.status === 'complete');
  let showPausePoint = $derived(PAUSE_POINT_STATUSES.has(job.status));
  let createdAt = $derived(new Date(job.created_at).toLocaleString());

  function handleRerun(stage) {
    rerunStage(job.id, stage);
  }

  function handleDelete() {
    if (window.confirm('Delete this job? This cannot be undone.')) {
      deleteJob(job.id);
    }
  }
</script>

<div class="card job-card border mb-3">
  <div class="card-body py-3 px-4">
    <div class="d-flex justify-content-between align-items-start">
      <div class="flex-grow-1 me-3">
        <div class="fw-semibold mb-1">
          {job.display_name}
        </div>
        <div class="drive-path text-muted">
          <i class="bi bi-hdd me-1"></i>{job.drive_path}
          {#if job.disc_label && job.disc_label !== job.display_name}
            <span class="ms-2 text-muted">&middot;</span>
            <span class="ms-2">{job.disc_label}</span>
          {/if}
        </div>
      </div>
      <div class="d-flex flex-column align-items-end gap-1">
        <span class="badge status-badge {statusClass}">
          {statusLabel}
        </span>
        {#if job.disc_type !== 'unknown'}
          <span class="badge status-badge bg-secondary">
            {#if job.disc_type === 'tv_show'}
              <i class="bi bi-tv me-1"></i>TV
            {:else}
              <i class="bi bi-camera-film me-1"></i>Movie
            {/if}
          </span>
        {/if}
      </div>
    </div>

    {#if showProgress}
      <div class="progress mt-3" style="height: 5px;">
        <div
          class="progress-bar bg-primary progress-bar-striped progress-bar-animated"
          role="progressbar"
          style="width: {job.progress}%"
          aria-valuenow={job.progress}
          aria-valuemin="0"
          aria-valuemax="100"
        ></div>
      </div>
    {/if}

    {#if job.error_message}
      <div class="mt-2 small text-danger">
        <i class="bi bi-exclamation-triangle-fill me-1"></i>{job.error_message}
      </div>
    {/if}

    {#if showPausePoint}
      <div class="pause-point-placeholder mt-3" data-status={job.status}></div>
    {/if}

    <div class="mt-2 small text-muted">
      <i class="bi bi-clock me-1"></i>{createdAt}
    </div>

    {#if showRetry}
      <div class="d-flex gap-2 mt-2 flex-wrap align-items-center">
        <span class="small text-muted me-2">Retry:</span>
        {#each RERUN_STAGES as { stage, label, icon } (stage)}
          <button
            type="button"
            class="btn btn-sm btn-outline-secondary"
            onclick={() => handleRerun(stage)}
          >
            <i class="bi {icon} me-1"></i>{label}
          </button>
        {/each}
      </div>
    {/if}

    {#if !job.is_active}
      <div class="d-flex justify-content-end mt-2">
        <button type="button" class="btn btn-sm btn-outline-danger" onclick={handleDelete}>
          <i class="bi bi-trash me-1"></i>Delete
        </button>
      </div>
    {/if}
  </div>
</div>
