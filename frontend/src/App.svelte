<script>
  import { onMount } from 'svelte';
  import { listJobs } from './lib/api.js';
  import { connectJobStream } from './lib/sse.js';
  import { jobList, setJobs, upsertJob, removeJob } from './lib/store.js';
  import JobCard from './components/JobCard.svelte';

  onMount(() => {
    listJobs().then(setJobs);

    const stream = connectJobStream({ onUpsert: upsertJob, onDelete: removeJob });
    return () => stream.close();
  });
</script>

<h1>Jacques</h1>

<div class="container-fluid">
  {#each $jobList as job (job.id)}
    <JobCard {job} />
  {/each}
</div>
