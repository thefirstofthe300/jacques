<script>
  // Pause-point form for JobCard when job.status is 'awaiting_selection' or
  // 'ripping_awaiting_selection'.
  import { untrack } from 'svelte';
  import { getCandidates, selectMatch } from '../lib/api.js';

  let { job } = $props();

  // Seeded once from the job prop's initial value on mount — intentionally
  // not kept in sync afterward, since job.id (and thus this component
  // instance) doesn't change without a full remount.
  let candidates = $state(untrack(() => job.candidates) ?? []);
  let loading = $state(false);
  let searched = $state(false);
  let manualId = $state('');
  let manualType = $state(untrack(() => job.disc_type) === 'tv_show' ? 'tv_show' : 'movie');

  async function search(discType) {
    loading = true;
    candidates = [];
    try {
      candidates = await getCandidates(job.id, discType);
    } finally {
      loading = false;
      searched = true;
    }
  }

  function handleSelect(candidate) {
    selectMatch(job.id, candidate.tmdb_id, candidate.disc_type);
  }

  function handleManualSubmit() {
    if (!manualId) return;
    selectMatch(job.id, manualId, manualType);
  }
</script>

<div class="candidate-selector mt-3">
  <div class="small text-warning mb-2">
    <i class="bi bi-question-circle-fill me-1"></i>Select the correct title:
  </div>
  <div class="d-flex gap-2 mb-2 align-items-center">
    <button type="button" class="btn btn-sm btn-outline-secondary" onclick={() => search('movie')}>
      <i class="bi bi-camera-film me-1"></i>Movie
    </button>
    <button type="button" class="btn btn-sm btn-outline-secondary" onclick={() => search('tv_show')}>
      <i class="bi bi-tv me-1"></i>TV Show
    </button>
    {#if loading}
      <span class="text-muted small ms-2">
        <span class="spinner-border spinner-border-sm" role="status"></span> Searching&hellip;
      </span>
    {/if}
  </div>

  {#if !loading && searched && candidates.length === 0}
    <div class="text-muted small mb-2">No matches found.</div>
  {/if}

  {#if candidates.length > 0}
    <div class="d-flex flex-column gap-2">
      {#each candidates as candidate (candidate.tmdb_id)}
        <div class="d-flex justify-content-between align-items-start border border-secondary rounded p-2">
          <div class="flex-grow-1 me-2">
            <div class="fw-semibold small">
              {candidate.title}
              {#if candidate.year}
                <span class="text-muted fw-normal">({candidate.year})</span>
              {/if}
              <span class="badge bg-secondary ms-1 fw-normal" style="font-size:0.65rem">
                {#if candidate.disc_type === 'tv_show'}
                  <i class="bi bi-tv me-1"></i>TV
                {:else}
                  <i class="bi bi-camera-film me-1"></i>Movie
                {/if}
              </span>
            </div>
            {#if candidate.overview}
              <div class="text-muted" style="font-size:0.75rem">{candidate.overview}</div>
            {/if}
          </div>
          <button
            type="button"
            class="btn btn-sm btn-outline-primary flex-shrink-0"
            onclick={() => handleSelect(candidate)}
          >
            Select
          </button>
        </div>
      {/each}
    </div>
  {/if}

  <div class="mt-2">
    <div class="small text-muted mb-1">
      <i class="bi bi-search me-1"></i>Or enter a TMDB ID directly:
    </div>
    <div class="d-flex gap-2 align-items-center flex-wrap">
      <input
        type="number"
        class="form-control form-control-sm bg-dark text-light border-secondary"
        style="max-width: 140px"
        placeholder="TMDB ID"
        min="1"
        bind:value={manualId}
      />
      <select
        class="form-select form-select-sm bg-dark text-light border-secondary"
        style="max-width: 110px"
        bind:value={manualType}
      >
        <option value="movie">Movie</option>
        <option value="tv_show">TV Show</option>
      </select>
      <button type="button" class="btn btn-sm btn-outline-secondary" onclick={handleManualSubmit}>
        Use this ID
      </button>
    </div>
  </div>
</div>
