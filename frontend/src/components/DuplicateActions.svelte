<script>
  // Pause-point form for JobCard when job.status === 'duplicate_detected'.
  import { rerip, deleteJob } from '../lib/api.js';

  let { job } = $props();

  let error = $state(null);

  async function handleRerip() {
    if (!window.confirm('Re-rip this disc? The previous rip will be kept.')) return;
    error = null;
    try {
      await rerip(job.id);
    } catch (err) {
      error = err.message;
    }
  }

  async function handleDismiss() {
    if (!window.confirm('Dismiss this job? It will be removed.')) return;
    error = null;
    try {
      await deleteJob(job.id);
    } catch (err) {
      error = err.message;
    }
  }
</script>

<div class="duplicate-actions mt-3">
  <div class="alert alert-info py-2 px-3 mb-2 small">
    <i class="bi bi-info-circle-fill me-1"></i>This disc was previously ripped.
    {#if job.disc_label}
      <div class="text-muted mt-1">Disc: {job.disc_label}</div>
    {/if}
  </div>
  <div class="d-flex gap-2 flex-wrap">
    <button type="button" class="btn btn-sm btn-outline-warning" onclick={handleRerip}>
      <i class="bi bi-arrow-repeat me-1"></i>Re-rip
    </button>
    <button type="button" class="btn btn-sm btn-outline-secondary" onclick={handleDismiss}>
      <i class="bi bi-x-circle me-1"></i>Dismiss
    </button>
  </div>

  {#if error}
    <div class="mt-2 small text-danger">
      <i class="bi bi-exclamation-triangle-fill me-1"></i>{error}
    </div>
  {/if}
</div>
