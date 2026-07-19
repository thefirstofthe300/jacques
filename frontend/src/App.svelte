<script>
  import { onMount } from 'svelte';
  import { connectJobStream } from './lib/sse.js';
  import { jobList, setJobs, upsertJob, removeJob } from './lib/store.js';
  import JobCard from './components/JobCard.svelte';

  onMount(() => {
    const stream = connectJobStream({ onResync: setJobs, onUpsert: upsertJob, onDelete: removeJob });
    return () => stream.close();
  });
</script>

<nav class="navbar navbar-dark" style="background-color: #161b22; border-bottom: 1px solid #30363d;">
  <div class="container-fluid px-4">
    <span class="navbar-brand">
      <i class="bi bi-disc-fill me-2" style="color: #58a6ff;"></i>Jacques
    </span>
    <span class="text-muted small">Disc Ripping Daemon</span>
  </div>
</nav>

<div class="container-fluid px-4 py-4 app-shell">
  {#if $jobList.length === 0}
    <div class="text-center py-5">
      <i class="bi bi-disc display-4 text-muted mb-3 d-block"></i>
      <p class="text-muted mb-1">No jobs yet.</p>
      <p class="text-muted small">Insert a disc to get started.</p>
    </div>
  {:else}
    {#each $jobList as job (job.id)}
      <JobCard {job} />
    {/each}
  {/if}
</div>
