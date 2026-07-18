<script>
  // Pause-point form for JobCard when job.status === 'awaiting_title_selection'.
  import { keepTitle } from '../lib/api.js';

  let { job } = $props();

  function formatDuration(seconds) {
    const total = seconds ?? 0;
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = Math.floor(total % 60);
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function handleKeep(title) {
    if (window.confirm('Keep this title? The other candidate rips will be discarded.')) {
      keepTitle(job.id, title.id);
    }
  }
</script>

<div class="title-selector mt-3">
  <div class="small text-warning mb-2">
    <i class="bi bi-question-circle-fill me-1"></i>Multiple candidate titles were ripped — pick which one
    to keep:
  </div>
  <div class="d-flex flex-column gap-2">
    {#each job.titles ?? [] as title (title.id)}
      <div class="d-flex justify-content-between align-items-center border border-secondary rounded p-2">
        <div class="flex-grow-1 me-2">
          <div class="fw-semibold small">
            {title.name || `Title ${title.id}`}
          </div>
          <div class="text-muted" style="font-size:0.75rem">
            <i class="bi bi-clock-history me-1"></i>{formatDuration(title.duration_seconds)}
          </div>
        </div>
        <button
          type="button"
          class="btn btn-sm btn-outline-primary flex-shrink-0"
          onclick={() => handleKeep(title)}
        >
          Keep this one
        </button>
      </div>
    {/each}
  </div>
</div>
