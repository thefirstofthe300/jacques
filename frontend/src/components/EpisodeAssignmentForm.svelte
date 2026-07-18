<script>
  // Pause-point form for JobCard when job.status === 'awaiting_episode_assignment'.
  import { untrack } from 'svelte';
  import { assignEpisodes } from '../lib/api.js';

  let { job } = $props();

  // Seeded once from the job prop's initial value on mount — intentionally
  // not kept in sync afterward, since job.id (and thus this component
  // instance) doesn't change without a full remount.
  let assignments = $state(
    (untrack(() => job.titles) ?? []).map((title, index) => ({
      title_id: title.id,
      season: 1,
      episode: index + 1,
      name: '',
    }))
  );

  function formatDuration(seconds) {
    const total = seconds ?? 0;
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = Math.floor(total % 60);
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function handleSave() {
    assignEpisodes(job.id, assignments);
  }
</script>

<div class="episode-assignment-form mt-3">
  <div class="small text-warning mb-2">
    <i class="bi bi-question-circle-fill me-1"></i>Assign season/episode/name to each ripped title:
  </div>
  <div class="d-flex flex-column gap-2">
    {#each job.titles ?? [] as title, index (title.id)}
      <div class="border border-secondary rounded p-2">
        <div class="fw-semibold small mb-1">
          {title.name || `Title ${title.id}`}
        </div>
        <div class="text-muted mb-2" style="font-size:0.75rem">
          <i class="bi bi-clock-history me-1"></i>{formatDuration(title.duration_seconds)}
        </div>
        <div class="d-flex gap-2 flex-wrap align-items-center">
          <input
            type="number"
            class="form-control form-control-sm bg-dark text-light border-secondary"
            style="max-width: 90px"
            placeholder="Season"
            min="1"
            bind:value={assignments[index].season}
          />
          <input
            type="number"
            class="form-control form-control-sm bg-dark text-light border-secondary"
            style="max-width: 90px"
            placeholder="Episode"
            min="1"
            bind:value={assignments[index].episode}
          />
          <input
            type="text"
            class="form-control form-control-sm bg-dark text-light border-secondary"
            style="max-width: 220px"
            placeholder="Episode name"
            bind:value={assignments[index].name}
          />
        </div>
      </div>
    {/each}
  </div>
  <div class="mt-2">
    <button type="button" class="btn btn-sm btn-outline-primary" onclick={handleSave}>
      Save &amp; Continue
    </button>
  </div>
</div>
